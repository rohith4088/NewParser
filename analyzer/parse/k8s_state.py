"""Parser for Kubernetes state manifests, pod statuses, and cluster events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Iterator

import yaml

from .timestamps import parse_iso_micros


@dataclass
class K8sFact:
    ts_micros: int | None
    kind: str
    namespace: str
    name: str
    reason: str
    exit_code: int | None
    restarts: int | None
    detail: str

    def to_tuple(self) -> tuple:
        return (
            self.ts_micros,
            self.kind,
            self.namespace,
            self.name,
            self.reason,
            self.exit_code,
            self.restarts,
            self.detail,
        )


def parse_k8s_yaml_stream(stream: BinaryIO, default_ns: str = "") -> Iterator[K8sFact]:
    """Extract OOM kills, restarts, exit codes, and events from Kubernetes YAML files."""
    try:
        content = stream.read().decode("utf-8", errors="replace")
        # Load multi-document yaml if needed
        docs = yaml.safe_load_all(content)
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            yield from _process_k8s_doc(doc, default_ns)
    except Exception:
        # Malformed yaml document shouldn't crash parser
        pass


def _process_k8s_doc(doc: dict, default_ns: str) -> Iterator[K8sFact]:
    kind = doc.get("kind", "")
    metadata = doc.get("metadata", {})
    ns = metadata.get("namespace") or default_ns or "default"
    name = metadata.get("name", "")

    # Handle List types (e.g. PodList, EventList)
    if kind.endswith("List") and "items" in doc:
        for item in doc.get("items", []):
            if isinstance(item, dict):
                yield from _process_k8s_doc(item, ns)
        return

    # 1. Pod state analysis
    if kind == "Pod":
        status = doc.get("status", {})
        container_statuses = status.get("containerStatuses", []) + status.get("initContainerStatuses", [])
        for cs in container_statuses:
            ctr_name = cs.get("name", "")
            restarts = cs.get("restartCount", 0)

            # Check lastState and state for terminated reasons (OOMKilled, Error)
            for state_key in ("lastState", "state"):
                state_dict = cs.get(state_key, {})
                terminated = state_dict.get("terminated")
                if isinstance(terminated, dict):
                    reason = terminated.get("reason", "")
                    exit_code = terminated.get("exitCode")
                    finished_at = terminated.get("finishedAt", "")
                    ts_micros = parse_iso_micros(finished_at) if finished_at else None

                    if reason in ("OOMKilled", "Error", "DeadlineExceeded", "ContainerCannotRun") or (exit_code and exit_code != 0):
                        yield K8sFact(
                            ts_micros=ts_micros,
                            kind="Pod",
                            namespace=ns,
                            name=f"{name}/{ctr_name}",
                            reason=reason or f"ExitCode:{exit_code}",
                            exit_code=exit_code,
                            restarts=restarts,
                            detail=f"Container {ctr_name} terminated in {state_key}: {reason} (code {exit_code})",
                        )

            # High restart alert
            if restarts > 3:
                yield K8sFact(
                    ts_micros=None,
                    kind="Pod",
                    namespace=ns,
                    name=f"{name}/{ctr_name}",
                    reason="CrashLoopBackOff",
                    exit_code=None,
                    restarts=restarts,
                    detail=f"Container {ctr_name} has restarted {restarts} times",
                )

    # 2. Event analysis
    elif kind == "Event":
        reason = doc.get("reason", "")
        message = doc.get("message", "")
        event_type = doc.get("type", "Normal")
        involved = doc.get("involvedObject", {})
        obj_name = involved.get("name") or name
        obj_kind = involved.get("kind") or "Resource"

        event_time = doc.get("eventTime") or doc.get("lastTimestamp") or metadata.get("creationTimestamp") or ""
        ts_micros = parse_iso_micros(event_time) if event_time else None

        if event_type == "Warning" or reason in ("Failed", "Unhealthy", "BackOff", "OOMKilled", "FailedScheduling"):
            yield K8sFact(
                ts_micros=ts_micros,
                kind=obj_kind,
                namespace=ns,
                name=obj_name,
                reason=reason,
                exit_code=None,
                restarts=None,
                detail=f"[{event_type}] {reason}: {message}",
            )
