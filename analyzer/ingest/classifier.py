"""Decide what kind of data a file holds.

Classification never assumes a fixed bundle layout. It combines three signals,
in this order:

1. **Content magic** (empty file, binary journal, NUL bytes => binary).
2. **Path rules** for the layouts we know. These are *hints*: a path rule only
   wins for text files.
3. **Content sniffing** of the first few KB (fluent JSON, syslog prefix, auditd
   records, Kubernetes YAML/JSON) for files at unfamiliar paths.

Anything left over is generic ``text`` so it is still counted and searchable
instead of being dropped. The function is pure (no I/O) so it is cheap to test.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum


class SourceType(str, Enum):
    FLUENT = "fluent"  # Fluent Bit container logs (JSON lines)
    POD_LOG = "pod_log"  # Pod container logs (.log) in k8s directories
    SYSLOG = "syslog"  # /var/log/messages, syslog, auth.log, kern.log ...
    AUDIT = "audit"  # auditd records
    JOURNAL = "journal"  # binary systemd journal
    K8S_STATE = "k8s_state"  # kubectl get/describe output, manifests
    K8S_EVENTS = "k8s_events"  # Kubernetes events
    USAGE = "usage"  # cpu/memory/disk/inode/ntp snapshots, kubectl top
    NETWORK = "network"  # ip addr, routes, hosts, resolv.conf
    DOCKER = "docker"  # host-level docker / control-plane service logs
    INSPECTION = "inspection"  # microk8s inspect / storage (mayastor) dumps
    METRICS = "metrics"  # Prometheus TSDB snapshot and similar
    TEXT = "text"  # readable text we have no parser for
    BINARY = "binary"  # unreadable binary
    EMPTY = "empty"
    ARCHIVE = "archive"  # archive deliberately left unexpanded

    def __str__(self) -> str:  # nicer in f-strings / tables
        return self.value


# Sources a bundle may or may not contain. Coverage reports "not found" for
# any of these that never appeared, instead of silently showing nothing.
PRIMARY_SOURCES: tuple[SourceType, ...] = (
    SourceType.FLUENT,
    SourceType.POD_LOG,
    SourceType.SYSLOG,
    SourceType.AUDIT,
    SourceType.JOURNAL,
    SourceType.K8S_STATE,
    SourceType.K8S_EVENTS,
    SourceType.USAGE,
    SourceType.NETWORK,
    SourceType.DOCKER,
    SourceType.INSPECTION,
    SourceType.METRICS,
)

JOURNAL_MAGIC = b"LPKSHHRH"


@dataclass(frozen=True)
class Classification:
    source_type: SourceType
    # "magic" | "path" | "content" | "fallback"
    reason: str
    hints: dict[str, str] = field(default_factory=dict)


# --- path knowledge -------------------------------------------------------------

_SYSLOG_NAMES = {
    "messages", "syslog", "secure", "auth.log", "kern.log", "kern", "dmesg",
    "cron", "maillog", "daemon.log", "boot.log", "user.log",
}
_NODE_SEGMENT = re.compile(r"^(?P<n>.+-node-\d+|node-?\d+)$", re.IGNORECASE)
_FLUENT_CONTAINER_DIR = re.compile(
    r"^kube\.var\.log\.containers\.(?P<pod>.+?)_(?P<ns>[^_]+)_(?P<ctr>.+)-[0-9a-f]{64}\.log$"
)
_ROTATION_SUFFIX = re.compile(r"(\.\d+)+(\.gz)?$")

# (regex on the lower-cased logical path, source type), first match wins.
_PATH_RULES: tuple[tuple[re.Pattern, SourceType], ...] = tuple(
    (re.compile(pattern), source)
    for pattern, source in (
        (r"(^|/)kube\.var\.log\.containers\.", SourceType.FLUENT),
        (r"(^|/)(fluent-logs?|cluster-logs)/", SourceType.FLUENT),
        (r"(^|/)kubectl_top[^/]*$", SourceType.USAGE),
        (r"(^|/)kubernetes/.*(^|/)events?/", SourceType.K8S_EVENTS),
        (r"(^|/)kubernetes/.*events[^/]*\.(yaml|yml|txt|json)$", SourceType.K8S_EVENTS),
        (r"\.events$", SourceType.K8S_EVENTS),
        (r"(^|/)kubernetes/.*(^|/)pods/logs/.*\.log$", SourceType.POD_LOG),
        (r"(^|/)pods/logs/.*\.log$", SourceType.POD_LOG),
        (r"(^|/)kubernetes/", SourceType.K8S_STATE),
        (r"(^|/)usage/", SourceType.USAGE),
        (r"(^|/)(cpu|disk|inode|memory|network)_usage(\.txt)?$", SourceType.USAGE),
        (r"(^|/)(mounted_filesystems|ntp_sync_status|filesystem_type|fsck)(\.txt)?$", SourceType.USAGE),
        (r"(^|/)network/", SourceType.NETWORK),
        (r"(^|/)(ifconfig|ip_addr_show|ip_route|resolv_conf|resolv\.conf)(\.txt)?$", SourceType.NETWORK),
        (r"(^|/)prometheus[^/]*/(snapshots?|prometheus-db|data)/", SourceType.METRICS),
        (r"(^|/)docker/", SourceType.DOCKER),
        (r"(^|/)(onprem-cli|docker_ps_output|admin-ui|admin_control|hlc|opm|sm_hlc|snapshot_mgr|traefik|tui)(\.log|\.txt)?$", SourceType.DOCKER),
        (r"(^|/)(inspection-report|microk8s-inspect|mayastor)[^/]*", SourceType.INSPECTION),
    )
)


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _syslog_by_name(path: str) -> bool:
    name = _ROTATION_SUFFIX.sub("", _basename(path).lower())
    return name in _SYSLOG_NAMES


def _audit_by_name(path: str) -> bool:
    return _ROTATION_SUFFIX.sub("", _basename(path).lower()) == "audit.log"


def path_hints(path: str) -> dict[str, str]:
    """Facts readable from the path alone: node name, k8s namespace/pod/container."""
    hints: dict[str, str] = {}
    segments = path.split("/")
    for segment in segments:
        match = _NODE_SEGMENT.match(segment)
        if match:
            hints["node"] = match.group("n")
            break
    for index, segment in enumerate(segments):
        match = _FLUENT_CONTAINER_DIR.match(segment)
        if match:
            hints["pod"] = match.group("pod")
            hints["namespace"] = match.group("ns")
            hints["container"] = match.group("ctr")
            break
        if segment in ("fluent-logs", "fluent-log", "fluent", "fluentbit", "cluster-logs") and index + 1 < len(segments) - 1:
            next_seg = segments[index + 1]
            ns_idx = index + 1
            if _NODE_SEGMENT.match(next_seg) and index + 2 < len(segments) - 1:
                ns_idx = index + 2
            hints.setdefault("namespace", segments[ns_idx])
            if ns_idx + 1 < len(segments) - 1:
                hints.setdefault("pod", segments[ns_idx + 1])
            if ns_idx + 2 < len(segments) - 1:
                hints.setdefault("container", segments[ns_idx + 2])
        if segment == "namespaces" and index + 1 < len(segments):
            hints.setdefault("namespace", segments[index + 1])
        if segment == "logs" and index > 0 and segments[index - 1] == "pods":
            fname = segments[-1]
            if fname.lower().endswith(".log"):
                base = fname[:-4]
                hints.setdefault("pod", base)
                hints.setdefault("container", base)
    return hints


def classify_path(path: str) -> SourceType | None:
    """Best guess from the path alone, or ``None`` if no rule matches."""
    if _audit_by_name(path):
        return SourceType.AUDIT
    if _syslog_by_name(path):
        return SourceType.SYSLOG
    lower = path.lower()
    if lower.endswith(".journal") or lower.endswith(".journal~"):
        return SourceType.JOURNAL
    for pattern, source in _PATH_RULES:
        if pattern.search(lower):
            return source
    return None


# --- content sniffing -----------------------------------------------------------

_BSD_SYSLOG = re.compile(r"^[A-Z][a-z]{2}\s+\d{1,2}\s\d{2}:\d{2}:\d{2}\s\S+\s")
_ISO_SYSLOG = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s+\S+\s+[\w./@-]+(?:\[\d+\])?:\s"
)
_AUDITD = re.compile(r"^type=[A-Z_]+ msg=audit\(\d+\.\d+:\d+\)")


def _first_lines(text: str, count: int = 5) -> list[str]:
    lines = []
    for line in text.splitlines():
        if line.strip():
            lines.append(line)
            if len(lines) == count:
                break
    return lines


def _mostly(lines: list[str], pattern: re.Pattern) -> bool:
    if not lines:
        return False
    hits = sum(1 for line in lines if pattern.match(line))
    return hits >= max(1, (len(lines) * 3 + 3) // 4)  # at least 75%


def _is_fluent_json(line: str) -> bool:
    line = line.strip()
    if not line.startswith("{"):
        return False
    try:
        data = json.loads(line)
    except ValueError:
        return False
    return isinstance(data, dict) and "kubernetes" in data and ("message" in data or "log" in data)


def _sniff_text(text: str) -> SourceType | None:
    lines = _first_lines(text)
    if not lines:
        return None
    if _is_fluent_json(lines[0]):
        return SourceType.FLUENT
    if _mostly(lines, _AUDITD):
        return SourceType.AUDIT
    if _mostly(lines, _BSD_SYSLOG) or _mostly(lines, _ISO_SYSLOG):
        return SourceType.SYSLOG
    stripped = text.lstrip()
    if re.match(r"(apiVersion|kind):\s", stripped) or re.search(r"^kind:\s+\w+", text, re.MULTILINE):
        return SourceType.K8S_STATE
    if stripped.startswith("{") and '"apiVersion"' in text and '"kind"' in text:
        return SourceType.K8S_STATE
    return None


def classify(
    path: str,
    head: bytes,
    size: int | None = None,
    is_archive: bool = False,
) -> Classification:
    """Classify one file from its logical path and its first bytes."""
    hints = path_hints(path)

    if is_archive:
        return Classification(SourceType.ARCHIVE, "magic", hints)
    if size == 0 or (not head and size is None):
        return Classification(SourceType.EMPTY, "magic", hints)
    if head.startswith(JOURNAL_MAGIC):
        return Classification(SourceType.JOURNAL, "magic", hints)
    if b"\x00" in head:
        # Prometheus blocks etc. are legitimately binary but still worth naming.
        by_path = classify_path(path)
        if by_path in (SourceType.METRICS, SourceType.INSPECTION):
            return Classification(by_path, "path", hints)
        return Classification(SourceType.BINARY, "magic", hints)

    by_path = classify_path(path)
    # A ".journal" path with plain-text content is not a journal.
    if by_path is not None and by_path is not SourceType.JOURNAL:
        return Classification(by_path, "path", hints)

    text = head.decode("utf-8", errors="replace")
    sniffed = _sniff_text(text)
    if sniffed is not None:
        return Classification(sniffed, "content", hints)
    return Classification(SourceType.TEXT, "fallback", hints)
