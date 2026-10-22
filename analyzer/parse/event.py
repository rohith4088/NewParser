"""Normalized event and log level definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Heuristic categorization patterns
_OOM_RE = re.compile(r"(?i)(oomkilled|out of memory|oom-killer|killed process \d+)")
_TIMEOUT_RE = re.compile(r"(?i)(timeout|timed out|context deadline exceeded)")
_CONN_REFUSED_RE = re.compile(r"(?i)(connection.*refused|dial tcp.*connect: connection refused)")
_PANIC_RE = re.compile(r"(?i)(panic:|fatal error: panic|segmentation fault|segfault)")
_DISK_FULL_RE = re.compile(r"(?i)(no space left on device|disk full|disk quota exceeded)")
_CERT_RE = re.compile(r"(?i)(certificate has expired|x509: certificate|cert.*expired)")
_FAIL_RE = re.compile(r"(?i)\b(errors?|exceptions?|fail(?:ed|ure|ures|s)?|fatal|critical)\b")
# CamelCase exception/error types, e.g. NullPointerException, KeyError
_FAIL_TYPE_RE = re.compile(r"[a-z](?:Exception|Error)\b")

# Phrases that contain an error word but report the ABSENCE of a problem.
# They are blanked out before heuristic level/issue detection so that lines
# like "sync done, errors=0" or "err=<nil>" are not counted as errors.
_BENIGN_RE = re.compile(
    r"(?i)"
    r"\b(?:errors?|err|failures?|failed|fails|exceptions?|warnings?)\s*[=:]\s*(?:0|none|nil|<nil>|null|false|\"\"|''|\[\]|\{\})(?![\w.])"
    r"|\"(?:error|err|exception|failure)\"\s*:\s*(?:null|false|\"\"|0|\[\]|\{\})"
    r"|\b(?:no|zero|0|without(?: any)?)\s+(?:errors?|failures?|exceptions?|faults?)\b"
    r"|\b(?:errors?|failures?)\s+(?:count|total)\s*[=:]?\s*0\b"
    r"|\berror[-_ ]?free\b"
)


def _scrub_benign(text: str) -> str:
    return _BENIGN_RE.sub(" ", text)


def detect_level(text: str, explicit_level: str = "") -> str:
    """Normalize log level (ERROR, WARN, INFO, DEBUG, FATAL)."""
    if explicit_level:
        lvl = explicit_level.strip().upper()
        if lvl in ("ERROR", "ERR"):
            return "ERROR"
        if lvl in ("WARN", "WARNING"):
            return "WARN"
        if lvl in ("INFO", "INFORMATIONAL"):
            return "INFO"
        if lvl in ("DEBUG", "DBG", "TRACE"):
            return "DEBUG"
        if lvl in ("FATAL", "CRITICAL", "PANIC"):
            return "FATAL"

    if _PANIC_RE.search(text):
        return "FATAL"
    if _OOM_RE.search(text):
        return "FATAL"

    upper = _scrub_benign(text[:200]).upper()
    if re.search(r"\b(FATAL|CRITICAL|PANIC)\b", upper):
        return "FATAL"
    if re.search(r"\b(ERROR|ERR|EXCEPTION)\b", upper):
        return "ERROR"
    if re.search(r"\b(WARN|WARNING)\b", upper):
        return "WARN"
    if re.search(r"\b(INFO)\b", upper):
        return "INFO"
    if re.search(r"\b(DEBUG|TRACE)\b", upper):
        return "DEBUG"

    return "INFO"


def detect_issue_type(message: str, level: str = "INFO") -> str:
    """Categorize the issue into an actionable triage bucket."""
    if _OOM_RE.search(message):
        return "OOM Killed"
    if _PANIC_RE.search(message):
        return "Panic/Crash"
    if _CERT_RE.search(message):
        return "Certificate Expired"
    if _CONN_REFUSED_RE.search(message):
        return "Connection Refused"
    if _TIMEOUT_RE.search(message):
        return "Timeout"
    if _DISK_FULL_RE.search(message):
        return "Disk Full"
    if level in ("ERROR", "FATAL"):
        return "General Error"
    scrubbed = _scrub_benign(message)
    if _FAIL_RE.search(scrubbed) or _FAIL_TYPE_RE.search(scrubbed):
        return "General Error"
    if level == "WARN":
        return "Warning"
    return "Info"


@dataclass
class Event:
    ts_micros: int | None
    ts_iso: str
    source: str
    node: str
    namespace: str
    pod: str
    container: str
    level: str
    issue_type: str
    message: str
    file: str
    line_num: int
    unit_id: str | None = None

    def to_tuple(self) -> tuple:
        return (
            self.ts_micros,
            self.ts_iso,
            self.source,
            self.node,
            self.namespace,
            self.pod,
            self.container,
            self.level,
            self.issue_type,
            self.message,
            self.file,
            self.line_num,
            self.unit_id,
        )
