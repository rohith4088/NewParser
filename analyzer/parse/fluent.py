"""Parser for Fluent Bit JSON container log streams."""

from __future__ import annotations

import json
from typing import BinaryIO, Iterator

try:
    import orjson
    _HAS_ORJSON = True
except ImportError:
    _HAS_ORJSON = False

from .cri import split_cri
from .event import Event, detect_issue_type, detect_level
from .stitch import LogStitcher, RawLogLine
from .timestamps import micros_to_iso, parse_any_ts


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


# Timestamp keys in priority order. Fluent Bit writes "date" (ISO string or a
# float epoch depending on json_date_format); other pipelines use the rest.
_TS_KEYS = ("date", "@timestamp", "time", "timestamp", "ts")

StreamKey = tuple[str, str, str]  # (namespace, pod, container)


def parse_fluent_stream(
    stream: BinaryIO,
    filename: str,
    default_node: str = "",
    default_namespace: str = "",
    default_pod: str = "",
    default_container: str = "",
) -> Iterator[Event]:
    """Parse a Fluent-Bit JSON log file line-by-line.

    Multi-line stack traces and runtime-split partial chunks arrive as
    separate JSON records. They are rejoined with one :class:`LogStitcher`
    per container stream (namespace/pod/container) so records from different
    containers in the same file are never merged. The metadata (timestamp,
    node, explicit level) of the first record of each stitched event is used.
    """
    line_num = 0
    stitchers: dict[StreamKey, LogStitcher] = {}
    # Per stream: physical line number -> metadata of that record.
    pending: dict[StreamKey, dict[int, dict]] = {}

    def _emit(key: StreamKey, stitched) -> Event:
        meta_by_line = pending[key]
        meta = meta_by_line.pop(stitched.start_line, None) or {}
        # Records swallowed into earlier events can no longer start one.
        for stale in [k for k in meta_by_line if k < stitched.start_line]:
            del meta_by_line[stale]
        msg = stitched.message.strip()
        level = detect_level(msg, meta.get("explicit_level", ""))
        return Event(
            ts_micros=meta.get("ts_micros"),
            ts_iso=meta.get("ts_iso", ""),
            source="fluent",
            node=meta.get("node", default_node or "node"),
            namespace=key[0],
            pod=key[1],
            container=key[2],
            level=level,
            issue_type=detect_issue_type(msg, level),
            message=msg,
            file=filename,
            line_num=stitched.start_line,
        )

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
        msg = str(msg)

        # Partial flag: Fluent Bit docker parser uses "logtag", CRI parser "_p"
        is_partial = data.get("logtag") == "P" or data.get("_p") == "P"

        # Timestamp
        ts_micros = None
        raw_ts = None
        for ts_key in _TS_KEYS:
            if data.get(ts_key) not in (None, ""):
                raw_ts = data[ts_key]
                ts_micros = parse_any_ts(raw_ts)
                if ts_micros is not None:
                    break

        # Unparsed CRI record forwarded verbatim in the "log" field
        cri = split_cri(msg.rstrip("\r\n"))
        if cri is not None:
            msg = cri.message
            is_partial = is_partial or cri.is_partial
            if ts_micros is None:
                ts_micros = cri.ts_micros

        # Partial chunks must keep boundary whitespace. Full lines keep their
        # leading indentation ("\tat com.Foo(...)") because the stitcher uses
        # it to recognise stack frames; the stitched message is trimmed later.
        msg = msg.rstrip("\r\n") if is_partial else msg.rstrip()

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
        node = data.get("host") or k8s.get("host") or default_node or "node"

        key = (ns, pod, container)
        stitcher = stitchers.get(key)
        if stitcher is None:
            stitcher = stitchers[key] = LogStitcher()
            pending[key] = {}
        pending[key][line_num] = {
            "ts_micros": ts_micros,
            "ts_iso": micros_to_iso(ts_micros) if ts_micros else ("" if raw_ts is None else str(raw_ts)),
            "node": node,
            "explicit_level": str(data.get("level") or data.get("severity") or ""),
        }

        stitched = stitcher.push(RawLogLine(line_num=line_num, text=msg, is_partial=is_partial))
        if stitched is not None:
            yield _emit(key, stitched)

    # Flush every stream at EOF
    for key, stitcher in stitchers.items():
        flushed = stitcher.flush()
        if flushed is not None:
            yield _emit(key, flushed)
