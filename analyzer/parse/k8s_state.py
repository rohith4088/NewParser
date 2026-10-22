"""Parser for Kubernetes state manifests, pod statuses, and cluster events."""

from __future__ import annotations

import re
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
    # How many times the underlying condition was observed. Kubernetes
    # aggregates repeated Events into one object with a ``count`` field, so a
    # single "BackOff" Event may stand for hundreds of occurrences.
    occurrences: int = 1

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
            self.occurrences,
        )


# Container ``state.waiting.reason`` values that mean the container cannot run.
# ("ContainerCreating" / "PodInitializing" are normal transient states.)
_BAD_WAITING_REASONS = frozenset({
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "ErrImageNeverPull",
    "InvalidImageName",
    "CreateContainerConfigError",
    "CreateContainerError",
    "RunContainerError",
    "ContainerCannotRun",
    "PreStartHookError",
    "PostStartHookError",
})

# Restart count above which a container is flagged even if it is currently
# running (it may have recovered, but it has been unstable).
_HIGH_RESTART_THRESHOLD = 3

_DOC_SPLIT = re.compile(r"^---\s*$", re.MULTILINE)


def _to_int(value: object, default: int | None = None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _ts(value: object) -> int | None:
    if not value:
        return None
    try:
        return parse_iso_micros(str(value))
    except ValueError:
        return None


def _iter_docs(content: str) -> Iterator[object]:
    """Yield YAML/JSON documents, surviving individual malformed documents.

    ``yaml.safe_load_all`` stops at the first bad document, which would drop
    every later Pod/Event in a multi-document dump. On failure we fall back to
    loading each ``---`` separated chunk on its own.
    """
    try:
        yield from list(yaml.safe_load_all(content))
        return
    except Exception:
        pass
    for chunk in _DOC_SPLIT.split(content):
        if not chunk.strip():
            continue
        try:
            yield yaml.safe_load(chunk)
        except Exception:
            continue


def parse_k8s_yaml_stream(stream: BinaryIO, default_ns: str = "") -> Iterator[K8sFact]:
    """Extract OOM kills, restarts, exit codes, and events from Kubernetes YAML files."""
    try:
        content = stream.read().decode("utf-8", errors="replace")
    except Exception:
        return
    for doc in _iter_docs(content):
        if not isinstance(doc, dict):
            continue
        try:
            yield from _process_k8s_doc(doc, default_ns)
        except Exception:
            # Malformed yaml document shouldn't crash parser
            continue


def _process_k8s_doc(doc: dict, default_ns: str) -> Iterator[K8sFact]:
    kind = doc.get("kind", "") or ""
    metadata = doc.get("metadata") or {}
    ns = metadata.get("namespace") or default_ns or "default"
    name = metadata.get("name", "")

    # Handle List types (e.g. PodList, EventList)
    if kind.endswith("List") and "items" in doc:
        for item in doc.get("items") or []:
            if isinstance(item, dict):
                yield from _process_k8s_doc(item, ns)
        return

    # 1. Pod state analysis
    if kind == "Pod":
        yield from _process_pod(doc, ns, name)

    # 2. Event analysis (core/v1 and events.k8s.io/v1)
    elif kind == "Event":
        yield from _process_event(doc, ns, name, metadata)


def _process_pod(doc: dict, ns: str, name: str) -> Iterator[K8sFact]:
    status = doc.get("status") or {}
    container_statuses = (status.get("containerStatuses") or []) + (status.get("initContainerStatuses") or [])
    for cs in container_statuses:
        if not isinstance(cs, dict):
            continue
        ctr_name = cs.get("name", "")
        restarts = _to_int(cs.get("restartCount"), 0) or 0
        full_name = f"{name}/{ctr_name}"
        last_failure_ts: int | None = None

        # Check lastState and state for terminated reasons (OOMKilled, Error)
        for state_key in ("lastState", "state"):
            state_dict = cs.get(state_key) or {}
            terminated = state_dict.get("terminated")
            if isinstance(terminated, dict):
                reason = terminated.get("reason", "") or ""
                exit_code = _to_int(terminated.get("exitCode"))
                ts_micros = _ts(terminated.get("finishedAt"))

                if reason in ("OOMKilled", "Error", "DeadlineExceeded", "ContainerCannotRun") or (exit_code and exit_code != 0):
                    last_failure_ts = last_failure_ts or ts_micros
                    yield K8sFact(
                        ts_micros=ts_micros,
                        kind="Pod",
                        namespace=ns,
                        name=full_name,
                        reason=reason or f"ExitCode:{exit_code}",
                        exit_code=exit_code,
                        restarts=restarts,
                        detail=f"Container {ctr_name} terminated in {state_key}: {reason} (code {exit_code})",
                    )

        # Current waiting state: the container is NOT running right now.
        waiting = (cs.get("state") or {}).get("waiting")
        waiting_reason = ""
        if isinstance(waiting, dict):
            waiting_reason = waiting.get("reason", "") or ""
            if waiting_reason in _BAD_WAITING_REASONS:
                message = (waiting.get("message") or "").strip()
                yield K8sFact(
                    ts_micros=last_failure_ts,
                    kind="Pod",
                    namespace=ns,
                    name=full_name,
                    reason=waiting_reason,
                    exit_code=None,
                    restarts=restarts,
                    detail=f"Container {ctr_name} waiting: {waiting_reason}" + (f" - {message}" if message else ""),
                )

        # High restart count, reported separately from a genuine
        # CrashLoopBackOff (a container with many historical restarts may be
        # running fine now).
        if restarts > _HIGH_RESTART_THRESHOLD and waiting_reason != "CrashLoopBackOff":
            yield K8sFact(
                ts_micros=last_failure_ts,
                kind="Pod",
                namespace=ns,
                name=full_name,
                reason="HighRestartCount",
                exit_code=None,
                restarts=restarts,
                detail=f"Container {ctr_name} has restarted {restarts} times",
            )


def _process_event(doc: dict, ns: str, name: str, metadata: dict) -> Iterator[K8sFact]:
    reason = doc.get("reason", "") or ""
    message = doc.get("message") or doc.get("note") or ""
    event_type = doc.get("type", "Normal") or "Normal"
    involved = doc.get("involvedObject") or doc.get("regarding") or {}
    obj_name = involved.get("name") or name
    obj_kind = involved.get("kind") or "Resource"

    series = doc.get("series") or {}
    occurrences = (
        _to_int(doc.get("count"))
        or _to_int(series.get("count"))
        or _to_int(doc.get("deprecatedCount"))
        or 1
    )

    # Prefer the most recent observation time.
    event_time = (
        doc.get("lastTimestamp")
        or series.get("lastObservedTime")
        or doc.get("eventTime")
        or doc.get("deprecatedLastTimestamp")
        or metadata.get("creationTimestamp")
        or ""
    )
    ts_micros = _ts(event_time)
    first_seen = doc.get("firstTimestamp") or doc.get("deprecatedFirstTimestamp")

    if event_type == "Warning" or reason in ("Failed", "Unhealthy", "BackOff", "OOMKilled", "FailedScheduling", "Evicted"):
        detail = f"[{event_type}] {reason}: {message}"
        if occurrences > 1:
            detail += f" (x{occurrences}" + (f" since {first_seen}" if first_seen else "") + ")"
        yield K8sFact(
            ts_micros=ts_micros,
            kind=obj_kind,
            namespace=ns,
            name=obj_name,
            reason=reason,
            exit_code=None,
            restarts=None,
            detail=detail,
            occurrences=occurrences,
        )
