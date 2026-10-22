"""Container Runtime Interface (CRI) log line format.

containerd / CRI-O write container logs as::

    2026-08-09T17:00:04.316759123Z stdout F message text
    2026-08-09T17:00:04.316759123Z stderr P first 16KB of a long line

``F`` marks a full line, ``P`` a partial chunk that continues on the next
record. Bundles carry these files under ``/var/log/pods`` or
``/var/log/containers``, and Fluent Bit sometimes forwards them unparsed in
its ``log`` field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .timestamps import parse_iso_micros

_CRI_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))"
    r" (?P<stream>stdout|stderr) (?P<tag>[PF]) ?(?P<msg>.*)$"
)


@dataclass(frozen=True)
class CriLine:
    ts_micros: int | None
    stream: str
    is_partial: bool
    message: str


def split_cri(line: str) -> CriLine | None:
    """Return the parsed CRI record, or ``None`` if the line is not CRI."""
    match = _CRI_RE.match(line)
    if not match:
        return None
    try:
        ts = parse_iso_micros(match.group("ts"))
    except ValueError:
        ts = None
    return CriLine(
        ts_micros=ts,
        stream=match.group("stream"),
        is_partial=match.group("tag") == "P",
        message=match.group("msg"),
    )
