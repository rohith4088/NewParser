"""Metadata-only bundle inventory and on-the-fly service mapping.

Scans the bundle archive or directory to discover:
- Parts (e.g. Cluster Logs, Host Snapshot - node-1)
- Groups (Services, Host OS, Cluster State, Metrics, Inspection, Other)
- Units (Individual service namespaces, syslog, docker, audit, journal, usage, network)

Extracts file counts, byte sizes, container names, and date ranges without
unpacking the files. Enriches services on the fly using Topology.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from ..topology import Topology, UNMAPPED_CATEGORY
from .archive import Limits, Member, walk
from .classifier import _FLUENT_CONTAINER_DIR, _NODE_SEGMENT, path_hints

# Date pattern in path: e.g. /YYYY/MM/DD/ or 2026-08-10 or 20260810
_PATH_DATE_RE = re.compile(r"(?:^|/)(\d{4})[/-](\d{2})[/-](\d{2})(?:$|/|\.|_)")
_COMPACT_DATE_RE = re.compile(r"(?:^|/)(\d{4})(\d{2})(\d{2})[T_]")


@dataclass
class Unit:
    id: str  # e.g. "cluster:services:ccs-system" or "host-node-1:host_logs:syslog"
    name: str  # Display name e.g. "ccs-system" or "Syslog / System Messages"
    part_id: str  # e.g. "cluster" or "host-node-1"
    group: str  # "services", "host_logs", "cluster_state", "metrics", "diagnostics", "other"
    source_type: str  # "fluent", "pod_log", "syslog", "docker", "audit", "journal", etc.
    file_count: int = 0
    total_bytes: int = 0
    date_min: str | None = None
    date_max: str | None = None
    namespace: str | None = None
    containers: list[str] = field(default_factory=list)
    pods: list[str] = field(default_factory=list)
    description: str = ""
    category: str = ""
    parseable: bool = True
    parsed: bool = False
    event_count: int = 0
    path_prefixes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Group:
    id: str
    name: str
    units: list[Unit] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "units": [u.to_dict() for u in self.units],
        }


@dataclass
class Part:
    id: str  # e.g. "cluster" or "host-node-1"
    name: str  # e.g. "Cluster Logs" or "Host Snapshot (node-1)"
    kind: str  # "cluster", "host", "other"
    file_count: int = 0
    total_bytes: int = 0
    groups: list[Group] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "groups": [g.to_dict() for g in self.groups],
        }


@dataclass
class Inventory:
    source: str
    total_files: int = 0
    total_bytes: int = 0
    parts: list[Part] = field(default_factory=list)
    services_map: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "parts": [p.to_dict() for p in self.parts],
            "services_map": self.services_map,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Inventory":
        parts: list[Part] = []
        for p in data.get("parts", []):
            groups: list[Group] = []
            for g in p.get("groups", []):
                units: list[Unit] = []
                for u in g.get("units", []):
                    units.append(Unit(**u))
                groups.append(Group(id=g["id"], name=g["name"], units=units))
            parts.append(
                Part(
                    id=p["id"],
                    name=p["name"],
                    kind=p.get("kind", "other"),
                    file_count=p.get("file_count", 0),
                    total_bytes=p.get("total_bytes", 0),
                    groups=groups,
                )
            )
        return cls(
            source=data.get("source", ""),
            total_files=data.get("total_files", 0),
            total_bytes=data.get("total_bytes", 0),
            parts=parts,
            services_map=data.get("services_map", {}),
        )

    def find_unit(self, unit_id: str) -> Unit | None:
        for part in self.parts:
            for group in part.groups:
                for unit in group.units:
                    if unit.id == unit_id:
                        return unit
        return None

    def get_all_units(self) -> list[Unit]:
        units: list[Unit] = []
        for part in self.parts:
            for group in part.groups:
                units.extend(group.units)
        return units


def _extract_date(path: str) -> str | None:
    """Extract a YYYY-MM-DD date string from path if present."""
    m = _PATH_DATE_RE.search(path)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m2 = _COMPACT_DATE_RE.search(path)
    if m2:
        return f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}"
    return None


def _update_date_range(unit: Unit, date_str: str | None) -> None:
    if not date_str:
        return
    if unit.date_min is None or date_str < unit.date_min:
        unit.date_min = date_str
    if unit.date_max is None or date_str > unit.date_max:
        unit.date_max = date_str


def _heuristic_category(namespace: str) -> str:
    """Intelligently categorize namespaces not found in static topology."""
    ns = namespace.lower()
    if any(k in ns for k in ("db", "postgres", "sql", "redis", "cassandra", "minio", "mongo", "druid", "store", "storage")):
        return "Databases & Data Stores"
    if any(k in ns for k in ("kafka", "queue", "rabbit", "nats", "broker", "event", "stream", "msg")):
        return "Event Streaming & Messaging"
    if any(k in ns for k in ("kube", "cert", "cni", "calico", "system", "metallb", "istio", "ingress", "network", "monitor", "prometheus")):
        return "Core Infrastructure & Platform"
    if any(k in ns for k in ("api", "app", "ui", "gateway", "auth", "portal", "user", "account", "service", "frontend")):
        return "Application Microservices & APIs"
    return "Discovered Services (Unmapped)"


def build_inventory(
    source_path: str | os.PathLike,
    topology: Topology | None = None,
    limits: Limits | None = None,
) -> Inventory:
    """Walk bundle metadata and build structured parts, groups, units, and service map."""
    source_str = str(source_path)
    inv = Inventory(source=source_str)

    # Temporary aggregates keyed by (part_id, group_id, unit_id)
    units_dict: dict[tuple[str, str, str], Unit] = {}
    parts_meta: dict[str, dict[str, Any]] = {}
    services_seen: dict[str, dict[str, Any]] = {}

    # Fast filter: skip expanding deep TSDB snapshots (850MB+) during metadata inventory
    def _inv_filter(path: str, is_archive: bool) -> bool:
        lower = path.lower()
        if is_archive and ("prometheus-db/snapshots" in lower or "snapshots/" in lower):
            return False
        return True

    for event in walk(source_path, limits=limits, path_filter=_inv_filter):
        if not isinstance(event, Member):
            continue
        member = event
        path = member.path
        size = member.size or 0
        inv.total_files += 1
        inv.total_bytes += size

        lower_path = path.lower()
        hints = path_hints(path)
        node = hints.get("node")

        # Determine Part
        if "cluster-logs" in lower_path:
            part_id = "cluster"
            part_name = "Cluster Logs (Container Microservices)"
            part_kind = "cluster"
        elif "host-snapshot" in lower_path or "hostlogssb" in lower_path or node or "var/log" in lower_path:
            n_label = node or "node-1"
            part_id = f"host-{n_label}"
            part_name = f"Host Snapshot ({n_label})"
            part_kind = "host"
        else:
            part_id = "other"
            part_name = "General Bundle Files"
            part_kind = "other"

        if part_id not in parts_meta:
            parts_meta[part_id] = {
                "name": part_name,
                "kind": part_kind,
                "file_count": 0,
                "total_bytes": 0,
            }
        parts_meta[part_id]["file_count"] += 1
        parts_meta[part_id]["total_bytes"] += size

        # Determine Group and Unit inside the Part
        group_id = "other"
        unit_id = f"{part_id}:general"
        unit_name = "General Files"
        source_type = "text"
        ns_name = None
        pod_name = hints.get("pod")
        ctr_name = hints.get("container")

        # 1. Cluster Logs: FluentBit Microservice Logs
        if part_kind == "cluster" and (
            "fluent" in lower_path
            or "cluster-logs" in lower_path
            or lower_path.endswith(".log")
            or hints.get("namespace")
        ):
            group_id = "services"
            ns_name = hints.get("namespace")
            if not ns_name:
                parts = [p for p in path.split("/") if p]
                for i, p in enumerate(parts):
                    if "cluster-logs" in p.lower() and i + 1 < len(parts):
                        ns_name = parts[i + 1]
                        break
            ns_name = ns_name or "default"
            unit_id = f"cluster:services:{ns_name}"
            unit_name = ns_name
            source_type = "fluent"

        # 2. Cluster Logs: GLP SCMI / Platform Manifests
        elif part_kind == "cluster" and "glp_scmi_support_bundle" in lower_path:
            group_id = "platform"
            unit_id = "cluster:platform:glp_scmi"
            unit_name = "GLP Platform Manifests & Deployments"
            source_type = "k8s_state"

        # 3. Cluster Logs: Prometheus Metrics
        elif part_kind == "cluster" and "prometheus" in lower_path:
            group_id = "metrics"
            unit_id = "cluster:metrics:prometheus"
            unit_name = "Prometheus TSDB Snapshots"
            source_type = "metrics"

        # 4. Host: Kubernetes Services (Pod logs, manifests, per-namespace events)
        elif part_kind == "host" and ("kubernetes/base/namespaces/" in lower_path or "namespaces/" in lower_path):
            group_id = "services"
            ns_name = hints.get("namespace")
            if not ns_name:
                parts = path.split("/")
                if "namespaces" in parts:
                    idx = parts.index("namespaces")
                    if idx + 1 < len(parts):
                        ns_name = parts[idx + 1]
            ns_name = ns_name or "default"
            unit_id = f"{part_id}:services:{ns_name}"
            unit_name = ns_name
            source_type = "pod_log" if lower_path.endswith(".log") else "k8s_state"

        # 5. Host: Cluster-wide State & CRDs
        elif part_kind == "host" and ("kubernetes/base/clusterinfo" in lower_path or "kubernetes/base/crds" in lower_path or "kubectl_top" in lower_path):
            group_id = "cluster_state"
            unit_id = f"{part_id}:cluster_state:k8s_cluster"
            unit_name = "Cluster Info, CRDs & Resource Usage"
            source_type = "k8s_state"

        # 6. Host: Backups (Velero / backup-restore)
        elif part_kind == "host" and "backup-restore" in lower_path:
            group_id = "cluster_state"
            unit_id = f"{part_id}:cluster_state:backup_restore"
            unit_name = "Velero Backup & Restore State"
            source_type = "k8s_state"

        # 7. Host: Syslog / System messages
        elif part_kind == "host" and any(
            lower_path.endswith(f) or f"/{f}" in lower_path
            for f in ("messages", "syslog", "kern.log", "auth.log", "dmesg", "secure")
        ):
            group_id = "host_logs"
            unit_id = f"{part_id}:host_logs:syslog"
            unit_name = "Syslog, Kernel & Auth Logs"
            source_type = "syslog"

        # 8. Host: Docker & Control Plane Daemons
        elif part_kind == "host" and ("docker/" in lower_path or any(
            lower_path.endswith(f) for f in ("onprem-cli.log", "admin-ui.log", "admin_control.log", "traefik.log", "opm.log")
        )):
            group_id = "host_logs"
            unit_id = f"{part_id}:host_logs:docker"
            unit_name = "Docker & Control Plane Daemons"
            source_type = "docker"

        # 9. Host: Audit Logs
        elif part_kind == "host" and "audit" in lower_path:
            group_id = "host_logs"
            unit_id = f"{part_id}:host_logs:audit"
            unit_name = "Auditd Security Records"
            source_type = "audit"

        # 10. Host: Systemd Journal
        elif part_kind == "host" and "journal" in lower_path:
            group_id = "host_logs"
            unit_id = f"{part_id}:host_logs:journal"
            unit_name = "Systemd Binary Journald"
            source_type = "journal"

        # 11. Host: Usage Metrics (CPU, Memory, Disk, Inodes, FSCK)
        elif part_kind == "host" and "usage/" in lower_path:
            group_id = "diagnostics"
            unit_id = f"{part_id}:diagnostics:usage"
            unit_name = "System Resource Snapshots & FSCK"
            source_type = "usage"

        # 12. Host: Network Configuration
        elif part_kind == "host" and "network/" in lower_path:
            group_id = "diagnostics"
            unit_id = f"{part_id}:diagnostics:network"
            unit_name = "Network Interfaces, Routes & DNS"
            source_type = "network"

        # 13. Host: Seed Inspection & Mayastor dumps
        elif part_kind == "host" and ("inspection-report" in lower_path or "mayastor" in lower_path or "onprem-seed" in lower_path):
            group_id = "diagnostics"
            unit_id = f"{part_id}:diagnostics:inspection"
            unit_name = "MicroK8s Inspection & Mayastor Dumps"
            source_type = "inspection"

        key = (part_id, group_id, unit_id)
        if key not in units_dict:
            cat = ""
            desc = ""
            if ns_name:
                cat = topology.category_of(ns_name) if topology else _heuristic_category(ns_name)
                desc = topology.describe(ns_name) if topology else ""
                if not cat or cat == UNMAPPED_CATEGORY:
                    cat = _heuristic_category(ns_name)

            units_dict[key] = Unit(
                id=unit_id,
                name=unit_name,
                part_id=part_id,
                group=group_id,
                source_type=source_type,
                namespace=ns_name,
                category=cat,
                description=desc,
                parseable=(source_type != "metrics"),
            )

        target_unit = units_dict[key]
        target_unit.file_count += 1
        target_unit.total_bytes += size

        date_val = _extract_date(path)
        _update_date_range(target_unit, date_val)

        if pod_name and pod_name not in target_unit.pods and len(target_unit.pods) < 20:
            target_unit.pods.append(pod_name)
        if ctr_name and ctr_name not in target_unit.containers and len(target_unit.containers) < 20:
            target_unit.containers.append(ctr_name)

        # Track service across bundle
        if ns_name:
            if ns_name not in services_seen:
                cat = topology.category_of(ns_name) if topology else _heuristic_category(ns_name)
                desc = topology.describe(ns_name) if topology else ""
                if not cat or cat == UNMAPPED_CATEGORY:
                    cat = _heuristic_category(ns_name)
                related_raw = topology.get_related(ns_name) if topology else []

                services_seen[ns_name] = {
                    "namespace": ns_name,
                    "category": cat,
                    "description": desc,
                    "related": related_raw,
                    "parts_present": set(),
                    "unit_ids": [],
                    "total_files": 0,
                    "total_bytes": 0,
                    "date_min": None,
                    "date_max": None,
                    "containers": set(),
                }

            s_info = services_seen[ns_name]
            s_info["parts_present"].add(part_id)
            if unit_id not in s_info["unit_ids"]:
                s_info["unit_ids"].append(unit_id)
            s_info["total_files"] += 1
            s_info["total_bytes"] += size
            if date_val:
                if s_info["date_min"] is None or date_val < s_info["date_min"]:
                    s_info["date_min"] = date_val
                if s_info["date_max"] is None or date_val > s_info["date_max"]:
                    s_info["date_max"] = date_val
            if ctr_name:
                s_info["containers"].add(ctr_name)

    # Human-friendly group display names
    group_titles = {
        "services": "Platform Services & Microservices",
        "host_logs": "Host Operating System & Daemon Logs",
        "cluster_state": "Kubernetes State, Manifests & CRDs",
        "diagnostics": "Diagnostics, Usage & System Dumps",
        "platform": "Installer & SCMI Packages",
        "metrics": "Metrics & Prometheus Snapshots",
        "other": "Other Bundle Files",
    }

    # Group units into parts
    parts_grouped: dict[str, dict[str, list[Unit]]] = {}
    for (part_id, group_id, _), unit in units_dict.items():
        parts_grouped.setdefault(part_id, {}).setdefault(group_id, []).append(unit)

    # Build final Part and Group objects
    for part_id, pmeta in sorted(parts_meta.items()):
        part_groups: list[Group] = []
        g_map = parts_grouped.get(part_id, {})

        # Order groups logically: services first for cluster, host_logs & services first for host
        order = ["services", "host_logs", "cluster_state", "diagnostics", "platform", "metrics", "other"]
        for gid in order:
            if gid in g_map:
                u_list = sorted(g_map[gid], key=lambda u: u.name.lower())
                part_groups.append(
                    Group(
                        id=gid,
                        name=group_titles.get(gid, gid.capitalize()),
                        units=u_list,
                    )
                )

        inv.parts.append(
            Part(
                id=part_id,
                name=pmeta["name"],
                kind=pmeta["kind"],
                file_count=pmeta["file_count"],
                total_bytes=pmeta["total_bytes"],
                groups=part_groups,
            )
        )

    # Convert sets to sorted lists in services_map
    clean_services_map: dict[str, dict[str, Any]] = {}
    for ns, s_info in sorted(services_seen.items()):
        clean_services_map[ns] = {
            "namespace": ns,
            "category": s_info["category"],
            "description": s_info["description"],
            "related": s_info["related"],
            "parts_present": sorted(s_info["parts_present"]),
            "unit_ids": s_info["unit_ids"],
            "total_files": s_info["total_files"],
            "total_bytes": s_info["total_bytes"],
            "date_min": s_info["date_min"],
            "date_max": s_info["date_max"],
            "containers": sorted(s_info["containers"]),
        }
    inv.services_map = clean_services_map

    return inv


def make_unit_path_filter(target_unit_ids: set[str], inventory: Inventory) -> Callable[[str, bool], bool]:
    """Build a fast walker path filter that only decompresses files matching target units."""
    if not target_unit_ids:
        return lambda path, is_archive: False

    # Extract target namespaces and sources
    target_namespaces: set[str] = set()
    wants_syslog = False
    wants_docker = False
    wants_audit = False
    wants_journal = False
    wants_k8s_cluster = False
    wants_backup = False
    wants_usage = False
    wants_network = False
    wants_inspection = False
    wants_glp = False

    wants_cluster = False
    wants_host = False

    for uid in target_unit_ids:
        unit = inventory.find_unit(uid)
        if not unit:
            continue
        if unit.part_id == "cluster":
            wants_cluster = True
        elif unit.part_id.startswith("host"):
            wants_host = True

        if unit.namespace:
            target_namespaces.add(unit.namespace)
        if unit.source_type == "syslog":
            wants_syslog = True
        elif unit.source_type == "docker":
            wants_docker = True
        elif unit.source_type == "audit":
            wants_audit = True
        elif unit.source_type == "journal":
            wants_journal = True
        elif unit.source_type == "usage":
            wants_usage = True
        elif unit.source_type == "network":
            wants_network = True
        elif unit.source_type == "inspection":
            wants_inspection = True
        elif "k8s_cluster" in uid:
            wants_k8s_cluster = True
        elif "backup_restore" in uid:
            wants_backup = True
        elif "glp_scmi" in uid:
            wants_glp = True

    def _filter(path: str, is_archive: bool) -> bool:
        lower = path.lower()

        # Archive level pruning
        if is_archive:
            if "host-snapshot" in lower or "hostlogssb" in lower:
                return wants_host
            if "cluster-logs" in lower:
                return wants_cluster
            if "prometheus" in lower:
                return False
            return True

        # Fast path checks for specific components
        if "fluent" in lower or "cluster-logs" in lower:
            if not wants_cluster:
                return False
            if target_namespaces:
                parts = path.split("/")
                for seg in ("fluent-logs", "fluentbit-logs", "fluent", "cluster-logs"):
                    if seg in parts:
                        idx = parts.index(seg)
                        if idx + 1 < len(parts):
                            ns = parts[idx + 1]
                            return ns in target_namespaces
                return False
            return True

        if "namespaces/" in lower:
            parts = path.split("/")
            if "namespaces" in parts:
                idx = parts.index("namespaces")
                if idx + 1 < len(parts):
                    ns = parts[idx + 1]
                    return ns in target_namespaces
            return False

        if "audit" in lower:
            return wants_audit
        if "docker/" in lower or "traefik" in lower:
            return wants_docker
        if "journal" in lower:
            return wants_journal
        if "usage/" in lower or "fsck/" in lower:
            return wants_usage
        if "network/" in lower:
            return wants_network
        if "inspection" in lower or "mayastor" in lower:
            return wants_inspection
        if "backup-restore" in lower:
            return wants_backup
        if "clusterinfo" in lower or "crds" in lower or "kubectl_top" in lower:
            return wants_k8s_cluster
        if any(lower.endswith(f) for f in ("syslog", "messages", "kern.log", "auth.log", "dmesg")):
            return wants_syslog
        if "glp_scmi" in lower:
            return wants_glp

        return True

    return _filter
