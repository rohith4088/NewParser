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
_INDENTED = re.compile(r"^\s{2,}\S+")

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
        self._in_traceback = False
        self._was_partial = False

    def push(self, line: RawLogLine) -> StitchedEvent | None:
        """Feed one line. Returns a StitchedEvent if an event just completed."""
        raw_text = line.text.rstrip("\r\n")

        # 1. Fluent Bit partial line handling
        if self._was_partial:
            self._lines.append(raw_text)
            self._was_partial = line.is_partial
            return None

        if line.is_partial:
            if not self._lines:
                self._current_start_line = line.line_num
            self._lines.append(raw_text)
            self._was_partial = True
            return None

        # 2. Check if current line starts a new traceback block
        starts_traceback = bool(
            _PYTHON_TRACEBACK.match(raw_text)
            or _GO_PANIC.match(raw_text)
            or ("Exception:" in raw_text and not raw_text.startswith(" "))
        )

        # 3. Check if current line is unequivocally the start of a brand new log record
        is_new_record = bool(_NEW_LOG_RE.match(raw_text))

        # 4. Check if line is a continuation of current event
        is_cont = not is_new_record and is_continuation_line(raw_text)

        if self._lines:
            can_continue_traceback = (
                self._in_traceback
                and not starts_traceback
                and not is_new_record
                and len(self._lines) < self.max_buffer_lines
            )

            if is_cont or can_continue_traceback:
                self._lines.append(raw_text)
                return None
            else:
                # Flush previous event
                event = self._flush()
                self._current_start_line = line.line_num
                self._lines = [raw_text]
                self._in_traceback = starts_traceback
                return event
        else:
            self._current_start_line = line.line_num
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
        self._in_traceback = False
        self._was_partial = False
        return event

