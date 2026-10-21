"""Parser for Fluent Bit JSON container log streams."""

from __future__ import annotations

import json
from typing import BinaryIO, Iterator

try:
    import orjson
    _HAS_ORJSON = True
except ImportError:
    _HAS_ORJSON = False

from .event import Event, detect_issue_type, detect_level
from .stitch import LogStitcher, RawLogLine
from .timestamps import micros_to_iso, parse_iso_micros


def _loads(data: bytes | str) -> dict | None:
    if _HAS_ORJSON and isinstance(data, bytes):
        try:
            return orjson.loads(data)
        except Exception:
            return None
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        return json.loads(data)
    except Exception:
        return None


def parse_fluent_stream(
    stream: BinaryIO,
    filename: str,
    default_node: str = "",
    default_namespace: str = "",
    default_pod: str = "",
    default_container: str = "",
) -> Iterator[Event]:
    """Parse a Fluent-Bit JSON log file line-by-line."""
    line_num = 0
    stitcher = LogStitcher()

    # Read line-by-line
    for raw_line in stream:
        line_num += 1
        raw_line_stripped = raw_line.strip()
        if not raw_line_stripped:
            continue

        data = _loads(raw_line_stripped)
        if not data or not isinstance(data, dict):
            continue

        # Extract message / log field
        msg = data.get("message")
        if msg is None:
            msg = data.get("log") or data.get("msg") or ""
        msg = str(msg).strip()

        # Check for partial flag (Fluent Bit 'P')
        is_partial = data.get("logtag") == "P"

        stitched = stitcher.push(RawLogLine(line_num=line_num, text=msg, is_partial=is_partial))
        if stitched is not None:
            # Yield previous completed stitched event
            pass  # Fluent JSON usually has per-line metadata, we yield below

        # Extract metadata
        k8s = data.get("kubernetes") if isinstance(data.get("kubernetes"), dict) else {}
        ns = k8s.get("namespace_name") or default_namespace or "default"
        pod = data.get("opsramp_filter_pod_name") or k8s.get("pod_name") or default_pod or "pod"
        container = (
            data.get("opsramp_filter_container_name")
            or k8s.get("container_name")
            or default_container
            or "container"
        )
        node = data.get("host") or default_node or "node"

        # Timestamp
        date_str = data.get("date") or data.get("@timestamp") or ""
        ts_micros = parse_iso_micros(date_str) if date_str else None
        ts_iso = micros_to_iso(ts_micros) if ts_micros else date_str

        # Level & Issue detection
        explicit_level = str(data.get("level") or data.get("severity") or "")
        level = detect_level(msg, explicit_level)
        issue_type = detect_issue_type(msg, level)

        yield Event(
            ts_micros=ts_micros,
            ts_iso=ts_iso,
            source="fluent",
            node=node,
            namespace=ns,
            pod=pod,
            container=container,
            level=level,
            issue_type=issue_type,
            message=msg,
            file=filename,
            line_num=line_num,
        )
