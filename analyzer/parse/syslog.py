"""Parser for Syslog, kernel, auth, and host process logs."""

from __future__ import annotations

import re
from typing import BinaryIO, Iterator

from .cri import split_cri
from .event import Event, detect_issue_type, detect_level
from .stitch import LogStitcher, RawLogLine
from .timestamps import (
    micros_to_iso,
    parse_iso_micros,
    parse_klog_micros,
    parse_syslog_micros,
)

# Host & Process regex for standard syslog:
# e.g., "Aug  9 00:00:45 dsccvm systemd[1]: message text"
_HEADER_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)\s+([A-Za-z0-9_./@-]+)(?:\[(\d+)\])?:\s*(.*)$"
)

# ISO-8601 timestamp prefix as written by apps and rsyslog high-precision mode.
_ISO_PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
# "<iso-ts> host proc[pid]: msg". The host must not be a log level word, so
# "<ts> INFO main: started" is not mistaken for host=INFO.
_ISO_HEADER_RE = re.compile(
    _ISO_PREFIX_RE.pattern
    + r"\s+(?!(?i:info|warn|warning|error|err|debug|trace|fatal|critical|notice)\b)"
    r"(?P<host>[A-Za-z0-9_.-]+)\s+(?P<proc>[A-Za-z0-9_./@-]+)(?:\[\d+\])?:\s(?P<msg>.*)$"
)


def parse_syslog_stream(
    stream: BinaryIO,
    filename: str,
    default_node: str = "host",
    default_year: int = 2026,
    source: str = "syslog",
    default_namespace: str = "host",
    default_pod: str = "",
    default_container: str = "",
) -> Iterator[Event]:
    """Parse text syslog / host / pod log stream with timestamp and klog extraction.

    Container logs in CRI format (``<ts> stdout F msg``) are unwrapped first:
    the runtime timestamp is kept per line and only the message is stitched,
    so multi-line stack traces written as separate CRI records are rejoined
    and ``P`` (partial) chunks are concatenated.
    """
    stitcher = LogStitcher()
    line_num = 0
    last_ts_micros: int | None = None
    # CRI runtime timestamps keyed by physical line number, consumed when the
    # stitched event that starts on that line is emitted.
    cri_ts: dict[int, int | None] = {}
    common = dict(
        default_namespace=default_namespace,
        default_pod=default_pod,
        default_container=default_container,
    )

    def _emit(stitched) -> Iterator[Event]:
        # Carry the most recent timestamp forward so undated records inherit it.
        nonlocal last_ts_micros
        forced = cri_ts.pop(stitched.start_line, None)
        for event in _process_stitched(
            stitched, filename, default_node, default_year, source, last_ts_micros,
            forced_ts_micros=forced, **common,
        ):
            if event.ts_micros is not None:
                last_ts_micros = event.ts_micros
            yield event

    for raw_bytes in stream:
        line_num += 1
        line_str = raw_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line_str.strip():
            continue

        is_partial = False
        cri = split_cri(line_str)
        if cri is not None:
            cri_ts[line_num] = cri.ts_micros
            line_str = cri.message
            is_partial = cri.is_partial

        stitched = stitcher.push(RawLogLine(line_num=line_num, text=line_str, is_partial=is_partial))
        if stitched is not None:
            yield from _emit(stitched)
            # Lines before the current one can no longer start an event.
            if cri_ts:
                for stale in [k for k in cri_ts if k < line_num]:
                    del cri_ts[stale]

    # Flush remaining
    flushed = stitcher.flush()
    if flushed is not None:
        yield from _emit(flushed)


def _process_stitched(
    stitched,
    filename: str,
    default_node: str,
    default_year: int,
    source: str,
    last_ts_micros: int | None,
    default_namespace: str = "host",
    default_pod: str = "",
    default_container: str = "",
    forced_ts_micros: int | None = None,
) -> Iterator[Event]:
    text = stitched.message
    first_line = text.split("\n", 1)[0].strip()

    ts_micros: int | None = None
    node = default_node
    process = filename
    body = text

    # 1. Try BSD Syslog timestamp
    bsd_micros, consumed = parse_syslog_micros(first_line, default_year=default_year)
    if bsd_micros:
        ts_micros = bsd_micros
        after_ts = first_line[consumed:].strip()
        match = _HEADER_RE.match(after_ts)
        if match:
            node = match.group(1)
            process = match.group(2)
            # Body is the remainder of first line + any continuation lines
            first_body = match.group(4)
            rest = text.split("\n", 1)[1] if "\n" in text else ""
            body = f"{first_body}\n{rest}".strip() if rest else first_body
        else:
            body = after_ts

    # 2. Try ISO timestamp
    if ts_micros is None:
        iso_micros = parse_iso_micros(first_line)
        if iso_micros:
            ts_micros = iso_micros
            rest = text.split("\n", 1)[1] if "\n" in text else ""
            header = _ISO_HEADER_RE.match(first_line)
            if header:
                # "<ts> host proc[pid]: msg" (rsyslog high-precision format)
                node = header.group("host")
                process = header.group("proc")
                first_body = header.group("msg")
            else:
                # "<ts> LEVEL msg" or "<ts> msg": keep everything after the
                # timestamp (the level word is needed for level detection).
                prefix = _ISO_PREFIX_RE.match(first_line)
                first_body = first_line[prefix.end():].strip() if prefix else first_line
            # Keep continuation lines (stack traces) attached to the body.
            body = f"{first_body}\n{rest}" if rest else first_body

    # 3. Check for inner klog timestamp inside body (e.g. microk8s/kubelet logs)
    klog_micros, _, klog_level = parse_klog_micros(body, default_year=default_year)
    if klog_micros:
        ts_micros = klog_micros

    # A container runtime (CRI) timestamp carries the full year and timezone,
    # so it is more trustworthy than anything recovered from the message text.
    if forced_ts_micros is not None:
        ts_micros = forced_ts_micros

    # If undated continuation line, inherit previous timestamp
    if ts_micros is None and last_ts_micros is not None:
        ts_micros = last_ts_micros

    ts_iso = micros_to_iso(ts_micros)

    # Level detection
    level = klog_level or detect_level(body)
    issue_type = detect_issue_type(body, level)

    pod_val = default_pod or process
    ctr_val = default_container or process

    yield Event(
        ts_micros=ts_micros,
        ts_iso=ts_iso,
        source=source,
        node=node,
        namespace=default_namespace,
        pod=pod_val,
        container=ctr_val,
        level=level,
        issue_type=issue_type,
        message=body,
        file=filename,
        line_num=stitched.start_line,
    )
