"""Full parsing pipeline linking Ingestion, Normalization, and DuckDB Storage."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..ingest import (
    Classification,
    Inventory,
    Manifest,
    SourceType,
    build_inventory,
    make_unit_path_filter,
    scan,
)
from .fluent import parse_fluent_stream
from .journal import parse_journal_stream
from .k8s_state import parse_k8s_yaml_stream
from .syslog import parse_syslog_stream
from .usage import parse_usage_stream

if TYPE_CHECKING:
    from ..store.duck import SessionStore


def resolve_unit_id_for_path(path: str, classification: Classification) -> str:
    """Map a member's path and classification to its canonical inventory unit_id."""
    lower = path.lower()
    hints = classification.hints
    node = hints.get("node", "node-1")
    ns = hints.get("namespace", "default")
    st = classification.source_type

    if "cluster-logs" in lower or st is SourceType.FLUENT:
        if st is SourceType.FLUENT or "fluent" in lower:
            return f"cluster:services:{ns}"
        if "glp_scmi" in lower:
            return "cluster:platform:glp_scmi"
        if "prometheus" in lower:
            return "cluster:metrics:prometheus"
        return "cluster:general"

    part_id = f"host-{node}"
    if "namespaces/" in lower or st is SourceType.POD_LOG:
        return f"{part_id}:services:{ns}"
    if st is SourceType.SYSLOG:
        return f"{part_id}:host_logs:syslog"
    if st is SourceType.DOCKER:
        return f"{part_id}:host_logs:docker"
    if st is SourceType.AUDIT:
        return f"{part_id}:host_logs:audit"
    if st is SourceType.JOURNAL:
        return f"{part_id}:host_logs:journal"
    if st is SourceType.USAGE:
        return f"{part_id}:diagnostics:usage"
    if st is SourceType.NETWORK:
        return f"{part_id}:diagnostics:network"
    if st is SourceType.INSPECTION:
        return f"{part_id}:diagnostics:inspection"
    if "backup-restore" in lower:
        return f"{part_id}:cluster_state:backup_restore"
    if "clusterinfo" in lower or "crds" in lower or "kubectl_top" in lower:
        return f"{part_id}:cluster_state:k8s_cluster"
    return f"{part_id}:general"


def make_namespace_path_filter(target_namespaces: set[str]) -> Callable[[str, bool], bool]:
    """Create a path filter that skips non-target files and archives before decompressing."""
    wants_host = bool(target_namespaces & {"systemd", "host", "docker", "onprem"})
    wants_cluster = bool(target_namespaces - {"systemd", "host"})

    def _filter(path: str, is_archive: bool) -> bool:
        lower = path.lower()
        if is_archive:
            if "host-snapshot" in lower or "hostlogssb" in lower:
                return wants_host
            if "cluster-logs" in lower:
                return wants_cluster
            if "prometheus" in lower:
                return False
            return True

        if "fluent-logs/" in lower or "fluentbit-logs/" in lower or "fluent/" in lower:
            parts = path.split("/")
            for seg in ("fluent-logs", "fluentbit-logs", "fluent", "fluent-log"):
                if seg in parts:
                    idx = parts.index(seg)
                    subparts = parts[idx + 1 :]
                    if any(sp in target_namespaces for sp in subparts):
                        return True
                    # Also check for kube.var.log.containers format
                    from ..ingest.classifier import _FLUENT_CONTAINER_DIR
                    for sp in subparts:
                        m = _FLUENT_CONTAINER_DIR.match(sp)
                        if m and m.group("ns") in target_namespaces:
                            return True
            return True

        if "hostlogssb" in lower or "journal" in lower or "var/log" in lower:
            return wants_host

        if "kubernetes/" in lower:
            parts = path.split("/")
            if "namespaces" in parts:
                idx = parts.index("namespaces")
                if idx + 1 < len(parts):
                    ns = parts[idx + 1]
                    return ns in target_namespaces
            return True

        return True

    return _filter


def parse_bundle_to_store(
    source_path: str | os.PathLike,
    store: SessionStore,
    session_id: str = "default",
    include_namespaces: set[str] | list[str] | None = None,
    units: set[str] | list[str] | None = None,
    inventory: Inventory | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> Manifest:
    """Parse a bundle (archive or directory) and populate the DuckDB store.

    If ``units`` is provided, only files matching the requested units are parsed
    and stored.
    If ``include_namespaces`` is provided, only files and containers belonging
    to the specified namespaces are decompressed and parsed.
    """
    file_count = 0
    now_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    target_units = set(units) if units else None
    target_ns = set(include_namespaces) if include_namespaces else None

    # Determine path filter
    path_filter: Callable[[str, bool], bool] | None = None
    if target_units:
        if inventory is None:
            inventory = build_inventory(source_path)
        path_filter = make_unit_path_filter(target_units, inventory)
        # Clean previous unit data for idempotent parsing
        for uid in target_units:
            store.delete_unit(uid)
            u = inventory.find_unit(uid)
            pid = u.part_id if u else "unknown"
            store.record_unit_start(uid, pid, now_utc)
    elif target_ns:
        path_filter = make_namespace_path_filter(target_ns)

    unit_counts: dict[str, dict[str, int]] = {}

    def _handle_member(member, classification: Classification) -> None:
        nonlocal file_count
        st = classification.source_type
        hints = classification.hints
        node = hints.get("node", "host")
        ns = hints.get("namespace", "default")
        pod = hints.get("pod", "")
        container = hints.get("container", "")

        resolved_unit = resolve_unit_id_for_path(member.path, classification)

        # If specific units requested, verify membership
        if target_units is not None and resolved_unit not in target_units:
            # Check if namespace matches any unit
            if not any(resolved_unit.endswith(f":{u}") or u in resolved_unit for u in target_units):
                return

        # Selective namespace checks (legacy compatibility)
        if target_ns is not None:
            if st is SourceType.FLUENT and ns != "default" and ns not in target_ns:
                return
            if st is SourceType.POD_LOG and ns not in target_ns:
                return
            if st is SourceType.JOURNAL and "systemd" not in target_ns and "host" not in target_ns:
                return
            if st in (SourceType.SYSLOG, SourceType.AUDIT, SourceType.DOCKER):
                if ns not in target_ns and "systemd" not in target_ns and "host" not in target_ns and "onprem" not in target_ns:
                    return
            if st is SourceType.USAGE:
                if "systemd" not in target_ns and "host" not in target_ns:
                    return

        file_count += 1
        if resolved_unit not in unit_counts:
            unit_counts[resolved_unit] = {"files": 0, "events": 0}
        unit_counts[resolved_unit]["files"] += 1

        if on_progress:
            unit_parsed = sum(c["files"] for c in unit_counts.values())
            on_progress(unit_parsed, member.path)

        # 1. Fluent Bit container logs
        if st is SourceType.FLUENT:
            events = [
                e
                for e in parse_fluent_stream(
                    member.stream,
                    filename=member.path,
                    default_node=node,
                    default_namespace=ns,
                    default_pod=pod,
                    default_container=container,
                )
                if target_ns is None or e.namespace in target_ns
            ]
            inserted = store.insert_events(events, unit_id=resolved_unit)
            unit_counts[resolved_unit]["events"] += inserted

        # 2. Kubernetes Pod container logs in host snapshot
        elif st is SourceType.POD_LOG:
            events = list(
                parse_syslog_stream(
                    member.stream,
                    filename=member.path,
                    default_node=node,
                    source="pod_log",
                    default_namespace=ns,
                    default_pod=pod,
                    default_container=container,
                )
            )
            inserted = store.insert_events(events, unit_id=resolved_unit)
            unit_counts[resolved_unit]["events"] += inserted

        # 3. Syslog, Audit, Docker control plane logs
        elif st in (SourceType.SYSLOG, SourceType.AUDIT, SourceType.DOCKER):
            events = list(
                parse_syslog_stream(
                    member.stream,
                    filename=member.path,
                    default_node=node,
                    source=st.value,
                    default_namespace="host" if st is not SourceType.DOCKER else "onprem",
                    default_pod=pod,
                    default_container=container,
                )
            )
            inserted = store.insert_events(events, unit_id=resolved_unit)
            unit_counts[resolved_unit]["events"] += inserted

        # 4. Kubernetes state manifests & events
        elif st in (SourceType.K8S_STATE, SourceType.K8S_EVENTS):
            facts = list(parse_k8s_yaml_stream(member.stream, default_ns=ns))
            store.insert_k8s_facts(facts)

        # 5. Host Usage metrics
        elif st is SourceType.USAGE:
            metrics = list(parse_usage_stream(member.stream, filename=member.path, node=node))
            store.insert_metrics(metrics)

        # 6. Systemd Binary Journals
        elif st is SourceType.JOURNAL:
            events = list(
                parse_journal_stream(
                    member.stream,
                    filename=member.path,
                    default_node=node,
                    source=st.value,
                )
            )
            inserted = store.insert_events(events, unit_id=resolved_unit)
            unit_counts[resolved_unit]["events"] += inserted

    # Stream through the bundle once (filtered if units or namespaces specified)
    manifest = scan(source_path, path_filter=path_filter, on_member=_handle_member)

    # Record unit completions in store
    end_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    if target_units:
        for uid in target_units:
            stats = unit_counts.get(uid, {"files": 0, "events": 0})
            pid = uid.split(":")[0] if ":" in uid else "unknown"
            store.record_unit_complete(uid, pid, stats["files"], stats["events"], end_utc)

    # Finalize session metadata
    store.set_metadata(
        session_id=session_id,
        filename=Path(source_path).name,
        scanned_at=end_utc,
        total_files=manifest.total_files,
    )

    return manifest
