"""Multi-line log event stitching.

Diagnostics bundles contain multi-line stack traces (Python, Java, Go panics)
and chunked container logs (Fluent Bit partial chunks with ``logtag: "P"``).
Without stitching, a single stack trace gets fragmented into dozens of lines,
scrambling timestamps and corrupting template mining.

This module detects continuation lines and groups them into a single coherent
log event before classification and database storage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

# Common stack trace indicators
_PYTHON_TRACEBACK = re.compile(r"^Traceback \(most recent call last\):")
_JAVA_CAUSED_BY = re.compile(r"^\s*Caused by:\s+\S+")
_JAVA_AT_FRAME = re.compile(r"^\s+at\s+[\w$./]+\([^)]+\)")
_GO_PANIC = re.compile(r"^(panic:\s+|fatal error:\s+)")
_GO_GOROUTINE = re.compile(r"^goroutine \d+ \[[^\]]+\]:")
_GO_FRAME = re.compile(r"^\t[/\w.-]+\.go:\d+")
# Go panic function lines, e.g. "main.(*Server).Run(0xc000123)" or
# "created by net/http.(*Server).Serve in goroutine 1"
_GO_FUNC_LINE = re.compile(r"^(?:created by \S+|\[signal .+\]|[\w./*()\[\]-]+\(.*\)(?: \+0x[0-9a-f]+)?)")
_INDENTED = re.compile(r"^(?:\s{2,}|\t)\S+")

_NEW_LOG_RE = re.compile(
    r"^("
    r"\d{4}-\d{2}-\d{2}"  # ISO-8601
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:"  # Syslog
    r"|[IWEF]\d{4}\s+\d{2}:"  # klog
    r"|\{\""  # JSON
    r"|\[(?:INFO|WARN|WARNING|ERROR|ERR|FATAL|CRITICAL|DEBUG|TRACE)\]"  # [LEVEL]
    r")",
    re.IGNORECASE,
)


def is_continuation_line(line: str) -> bool:
    """Check if a log line is an unwrapped continuation of the previous line."""
    if not line:
        return False
    if _JAVA_AT_FRAME.match(line) or _JAVA_CAUSED_BY.match(line):
        return True
    if _GO_GOROUTINE.match(line) or _GO_FRAME.match(line):
        return True
    if line.startswith("  File \"") or line.strip().startswith("raise "):
        return True
    if _INDENTED.match(line):
        # Indented line often belongs to preceding error
        return True
    return False


@dataclass
class RawLogLine:
    line_num: int
    text: str
    is_partial: bool = False  # e.g. logtag: "P"


@dataclass
class StitchedEvent:
    start_line: int
    message: str
    continuation_lines: int = 0


class LogStitcher:
    """Stitches lines in a stream into multi-line events with lookahead."""

    def __init__(self, max_buffer_lines: int = 150):
        self.max_buffer_lines = max_buffer_lines
        self._current_start_line = 0
        self._lines: list[str] = []
        # None, "python", "go" or "java": decides which lines may extend the
        # current stack trace (see _push_physical_line).
        self._in_traceback: str | None = None
        self._was_partial = False

    def push(self, line: RawLogLine) -> StitchedEvent | None:
        """Feed one line. Returns a StitchedEvent if an event just completed."""
        raw_text = line.text.rstrip("\r\n")

        # 1. Partial chunk handling (Fluent Bit ``logtag: P`` / CRI ``P``).
        # A partial chunk is a fragment of ONE physical line that the container
        # runtime split (typically at 16KB). The following chunk therefore
        # belongs to the same line and is concatenated without a newline.
        if self._was_partial and self._lines:
            self._lines[-1] += raw_text
            self._was_partial = line.is_partial
            return None

        event = self._push_physical_line(line.line_num, raw_text)
        # The first chunk of a split line went through normal new-record /
        # continuation detection above; remember to glue the next chunk on.
        self._was_partial = line.is_partial
        return event

    def _push_physical_line(self, line_num: int, raw_text: str) -> StitchedEvent | None:
        # 2. Check if current line starts a new traceback block
        starts_traceback: str | None = None
        if _PYTHON_TRACEBACK.match(raw_text):
            starts_traceback = "python"
        elif _GO_PANIC.match(raw_text):
            starts_traceback = "go"
        elif "Exception:" in raw_text and not raw_text.startswith(" "):
            starts_traceback = "java"

        # 3. Check if current line is unequivocally the start of a brand new log record
        is_new_record = bool(_NEW_LOG_RE.match(raw_text))

        # 4. Check if line is a continuation of current event
        is_cont = not is_new_record and is_continuation_line(raw_text)

        if self._lines:
            room = len(self._lines) < self.max_buffer_lines
            free_line = not starts_traceback and not is_new_record and room

            if is_cont:
                self._lines.append(raw_text)
                return None
            if free_line and self._in_traceback == "python":
                # Python ends with one unindented "SomeError: message" line
                # after the frames; anything after that is a new event.
                self._lines.append(raw_text)
                self._in_traceback = None
                return None
            if free_line and self._in_traceback == "go" and _GO_FUNC_LINE.match(raw_text):
                # Go frames alternate unindented function lines with
                # tab-indented file:line lines.
                self._lines.append(raw_text)
                return None

            # Flush previous event (Java traces only continue through
            # "at ..." / "Caused by:" / indented lines, handled by is_cont).
            event = self._flush()
            self._current_start_line = line_num
            self._lines = [raw_text]
            self._in_traceback = starts_traceback
            return event
        else:
            self._current_start_line = line_num
            self._lines = [raw_text]
            self._in_traceback = starts_traceback
            return None

    def flush(self) -> StitchedEvent | None:
        """Flush any remaining buffered lines at EOF."""
        if not self._lines:
            return None
        return self._flush()

    def _flush(self) -> StitchedEvent:
        msg = "\n".join(self._lines)
        count = len(self._lines) - 1
        event = StitchedEvent(
            start_line=self._current_start_line,
            message=msg,
            continuation_lines=count,
        )
        self._lines = []
        self._in_traceback = None
        self._was_partial = False
        return event

