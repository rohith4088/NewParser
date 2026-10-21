"""Timestamp normalization into microsecond UTC epochs.

Air-gapped bundles collect logs across many systems, each formatting dates
differently:
- ISO-8601 / RFC-3339: ``2026-08-09T17:00:04.316759Z``, ``2026-08-09 17:00:04.316``
- Fluent Bit JSON: ``2026-08-09T17:00:04.316759Z`` or ``@timestamp``
- BSD Syslog: ``Aug  9 00:00:45`` (no year)
- Kubernetes klog: ``W0809 00:00:45.726460`` (month-day, no year)
- Apache / standard: ``09/Aug/2026:17:00:04 +0000``

This module converts every format into microsecond-precision integer UTC epochs
(microseconds since 1970-01-01 00:00:00 UTC). Microseconds ensure accurate
cross-node event sequencing and fit cleanly in DuckDB BIGINT.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

# Common month mapping for fast parsing
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Regex for ISO-8601 / RFC-3339 formats
# e.g., 2026-08-09T17:00:04.316759Z or 2026-08-09 17:00:04
_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2})[:.](\d{2})(?:\.(\d+))?(?:Z|([+-]\d{2}):?(\d{2}))?"
)

# Regex for BSD Syslog
# e.g., Aug  9 00:00:45 or Aug 09 00:00:45.123
_BSD_RE = re.compile(
    r"^([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
)

# Regex for Kubernetes klog prefix:
# e.g., W0809 00:00:45.726460 or E0809 00:00:45.733198
_KLOG_RE = re.compile(
    r"^[IWEF](\d{2})(\d{2})\s+(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
)


def parse_iso_micros(iso_str: str) -> int | None:
    """Parse ISO8601 / RFC3339 string to microsecond epoch."""
    if not iso_str:
        return None
    match = _ISO_RE.match(iso_str.strip())
    if not match:
        return None

    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))
    hour = int(match.group(4))
    minute = int(match.group(5))
    second = int(match.group(6))
    frac_str = match.group(7) or ""
    # Truncate or pad fraction to 6 digits (microseconds)
    micros = int(frac_str[:6].ljust(6, "0")) if frac_str else 0

    tz_hr = match.group(8)
    tz_min = match.group(9)

    dt = datetime(year, month, day, hour, minute, second, micros, tzinfo=timezone.utc)
    epoch_micros = int(dt.timestamp() * 1_000_000)

    # Apply timezone offset if present
    if tz_hr and tz_min:
        offset_mins = int(tz_hr) * 60 + (int(tz_min) if int(tz_hr) >= 0 else -int(tz_min))
        epoch_micros -= offset_mins * 60 * 1_000_000

    return epoch_micros


def parse_syslog_micros(line_or_ts: str, default_year: int = 2026) -> tuple[int | None, int]:
    """Parse BSD Syslog timestamp ("Aug  9 00:00:45") to microsecond epoch.

    Returns ``(micros, matched_len)`` where matched_len is the number of chars
    consumed from the start of the line.
    """
    match = _BSD_RE.match(line_or_ts.strip())
    if not match:
        return None, 0

    month_str = match.group(1).lower()
    month = _MONTHS.get(month_str)
    if not month:
        return None, 0

    day = int(match.group(2))
    hour = int(match.group(3))
    minute = int(match.group(4))
    second = int(match.group(5))
    frac_str = match.group(6) or ""
    micros = int(frac_str[:6].ljust(6, "0")) if frac_str else 0

    try:
        dt = datetime(default_year, month, day, hour, minute, second, micros, tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000_000), match.end()
    except ValueError:
        return None, 0


def parse_klog_micros(line: str, default_year: int = 2026) -> tuple[int | None, int, str]:
    """Parse klog prefix ("E0809 00:00:45.733198").

    Returns ``(micros, matched_len, level)`` where level is 'ERROR', 'WARN', 'INFO'.
    """
    match = _KLOG_RE.match(line.strip())
    if not match:
        return None, 0, ""

    prefix_char = line.strip()[0]
    level_map = {"I": "INFO", "W": "WARN", "E": "ERROR", "F": "FATAL"}
    level = level_map.get(prefix_char, "INFO")

    month = int(match.group(1))
    day = int(match.group(2))
    hour = int(match.group(3))
    minute = int(match.group(4))
    second = int(match.group(5))
    frac_str = match.group(6) or ""
    micros = int(frac_str[:6].ljust(6, "0")) if frac_str else 0

    try:
        dt = datetime(default_year, month, day, hour, minute, second, micros, tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000_000), match.end(), level
    except ValueError:
        return None, 0, ""


def micros_to_iso(micros: int | None) -> str:
    """Format microsecond epoch back into standard ISO-8601 UTC string."""
    if micros is None:
        return ""
    dt = datetime.fromtimestamp(micros / 1_000_000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
