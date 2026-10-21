"""Parser for Syslog, kernel, auth, and host process logs."""

from __future__ import annotations

import re
from typing import BinaryIO, Iterator

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
    """Parse text syslog / host / pod log stream with timestamp and klog extraction."""
    stitcher = LogStitcher()
    line_num = 0
    last_ts_micros: int | None = None

    for raw_bytes in stream:
        line_num += 1
        line_str = raw_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line_str.strip():
            continue

        stitched = stitcher.push(RawLogLine(line_num=line_num, text=line_str))
        if stitched is not None:
            yield from _process_stitched(
                stitched, filename, default_node, default_year, source, last_ts_micros,
                default_namespace=default_namespace,
                default_pod=default_pod,
                default_container=default_container,
            )

    # Flush remaining
    flushed = stitcher.flush()
    if flushed is not None:
        yield from _process_stitched(
            flushed, filename, default_node, default_year, source, last_ts_micros,
            default_namespace=default_namespace,
            default_pod=default_pod,
            default_container=default_container,
        )


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
            # Strip ISO prefix if possible
            parts = first_line.split(" ", 2)
            if len(parts) >= 3:
                body = parts[2]

    # 3. Check for inner klog timestamp inside body (e.g. microk8s/kubelet logs)
    klog_micros, _, klog_level = parse_klog_micros(body, default_year=default_year)
    if klog_micros:
        ts_micros = klog_micros

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
