"""Service topology: which namespace belongs to which component category.

Loaded from ``topology/services.yaml``. The topology is *descriptive only*:
namespaces actually present in a bundle are always used, and any namespace the
file does not know simply falls into the ``Other`` category and can be listed
via :meth:`Topology.unmapped`. A bundle is never rejected because of topology.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

UNMAPPED_CATEGORY = "Other"

DEFAULT_DEPENDENCIES: dict[str, dict[str, str]] = {
    "unified-api": {
        "ccs-system": "Routes core platform APIs and user account workflows",
        "postgresql-ccs": "Direct relational persistence for API configurations",
        "kafka-ccs": "Asynchronous messaging and audit events stream",
        "base": "OpenBao/Vault token verification and ingress secrets",
    },
    "ccs-system": {
        "postgresql-ccs": "Primary relational database for accounts and licensing",
        "rabbitmq-system": "Message broker for Celery worker & doorway queues",
        "redis-ccs": "Session cache and ephemeral key-value store",
        "kafka-ccs": "Audit trail logging pipeline and event bus",
        "base": "OpenBao/Vault credentials and TLS certificates",
        "ccs-pingfederate": "Enterprise SSO and identity token exchange",
    },
    "onprem": {
        "base": "Internal TLS cert-manager lifecycle and OpenBao credentials",
        "systemd": "Host operating system services, Docker daemon, and systemd journals",
        "mayastor": "Storage plane volume health and dynamic provisioning",
        "kube-system": "Node discovery, DNS resolution, and pod cleanup jobs",
    },
    "cassandra": {
        "cassandra-ccs": "Paired CCS platform keyspaces and schema migrations",
        "mayastor": "Underlying high-performance block PVC storage attachments",
        "systemd": "Host RAM and disk I/O pressure affecting Cassandra nodes",
    },
    "cassandra-ccs": {
        "cassandra": "Core Cassandra distributed database cluster",
        "ccs-system": "Cloud Control Service application consumers",
        "mayastor": "Persistent volume claims on host NVMe storage",
    },
    "postgresql-ccs": {
        "cnpg-system": "CloudNativePG cluster operator, failover, and connection pooling",
        "ccs-system": "Application consumer holding active connection pools",
        "mayastor": "Underlying PVC storage persistence",
        "base": "Database credentials stored in OpenBao/Vault",
    },
    "postgresql-dscc": {
        "cnpg-system": "CloudNativePG operator and PostgreSQL cluster controller",
        "kafka-dscc": "DSCC telemetry ingestion and data pipelines",
        "mayastor": "Persistent storage volumes",
    },
    "cnpg-system": {
        "postgresql-ccs": "Managed PostgreSQL CCS instance",
        "postgresql-dscc": "Managed PostgreSQL DSCC instance",
        "kube-system": "K8s API server webhook controller",
    },
    "kafka-ccs": {
        "kafka-operator": "Strimzi Kafka cluster operator and topic reconciliation",
        "ccs-system": "Audit-trail and notification publishers/consumers",
        "zk-druid": "Zookeeper metadata coordination quorum",
        "mayastor": "Kafka broker log partition PVC storage",
    },
    "kafka-dscc": {
        "kafka-operator": "Strimzi Kafka operator",
        "postgresql-dscc": "DSCC analytics sinks",
    },
    "kafka-operator": {
        "kafka-ccs": "Managed CCS Kafka messaging cluster",
        "kafka-dscc": "Managed DSCC Kafka messaging cluster",
        "kube-system": "Kubernetes CRD controllers and pod scheduling",
    },
    "rabbitmq-system": {
        "ccs-system": "Celery worker and task queue consumer",
    },
    "redis-ccs": {
        "ccs-system": "Cloud Control Service session and cache consumer",
    },
    "redis-dscc": {
        "kafka-dscc": "DSCC real-time telemetry caching",
    },
    "druid": {
        "zk-druid": "Apache Zookeeper distributed lock coordination",
        "minio": "MinIO S3 object store for deep storage segments",
        "base": "OpenBao/Vault secret injection sidecars",
    },
    "zk-druid": {
        "druid": "Real-time analytics engine coordination",
        "kafka-ccs": "Event streaming cluster coordination",
    },
    "minio": {
        "minio-operator": "MinIO tenant controller",
        "druid": "Deep storage consumer for analytics segments",
        "mayastor": "Underlying storage backend for S3 buckets",
    },
    "systemd": {
        "kube-system": "Host OS underlying Kubelet, containerd, and systemd units",
        "onprem": "Host-level onprem-cli, Docker daemons, and system scripts",
        "mayastor": "NVMe-oF kernel drivers, hugepages, and host block devices",
    },
    "kube-system": {
        "systemd": "Host OS kernel, journald, and Kubelet service",
        "base": "Platform security, admission webhooks, and CNI networking",
        "acp-system": "Node discovery watcher and cluster agent",
    },
    "base": {
        "ccs-system": "Vault secrets injection for microservices",
        "onprem": "Automated certificate management and rotation",
        "unified-api": "Ingress TLS certificates and authentication tokens",
        "kube-system": "ValidatingWebhookConfiguration and cert-manager CA",
    },
    "mayastor": {
        "systemd": "Host disk devices, NVMe-oF target controllers, and memory hugepages",
        "cassandra": "Database persistent volumes and storage pools",
        "postgresql-ccs": "PostgreSQL data and WAL persistent volumes",
    },
    "acp-system": {
        "kube-system": "K8s API discovery and node status watcher",
        "ccs-system": "Account and inventory registration",
    },
    "monitoring": {
        "kube-system": "Node exporter and kube-state-metrics",
        "systemd": "Host resource utilization counters",
    },
}


class TopologyError(ValueError):
    """Raised when the topology file is malformed."""


@dataclass(frozen=True)
class NamespaceInfo:
    name: str
    category: str
    category_id: str
    description: str
    containers: tuple[str, ...]
    container_descriptions: dict[str, str]
    related: tuple[str, ...] = ()
    related_reasons: dict[str, str] = field(default_factory=dict)


class Topology:
    def __init__(self, categories: list[dict]):
        self._categories = categories
        self._by_namespace: dict[str, NamespaceInfo] = {}
        for category in categories:
            for ns, entry in category.get("namespaces", {}).items():
                if ns in self._by_namespace:
                    raise TopologyError(
                        f"Namespace {ns!r} is listed in more than one category"
                    )
                entry = entry or {}
                # Merge explicit related with default dependencies
                default_rel = DEFAULT_DEPENDENCIES.get(ns, {})
                file_rel = entry.get("related_reasons") or entry.get("related") or {}
                if isinstance(file_rel, list):
                    merged_reasons = {**default_rel, **{r: "Related service dependency" for r in file_rel}}
                else:
                    merged_reasons = {**default_rel, **file_rel}

                self._by_namespace[ns] = NamespaceInfo(
                    name=ns,
                    category=category["name"],
                    category_id=category["id"],
                    description=entry.get("description", ""),
                    containers=tuple(entry.get("containers", [])),
                    container_descriptions=dict(entry.get("container_descriptions", {})),
                    related=tuple(merged_reasons.keys()),
                    related_reasons=merged_reasons,
                )

    @classmethod
    def load(cls, path: Path | str) -> "Topology":
        """Load a topology file. A missing file gives an empty topology.

        Missing is allowed on purpose: the analyzer must still work (with every
        namespace under ``Other``) if someone deletes or has not shipped it.
        """
        path = Path(path)
        if not path.exists():
            return cls([])
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise TopologyError(f"Cannot parse {path}: {exc}") from exc
        categories = data.get("categories")
        if not isinstance(categories, list):
            raise TopologyError(f"{path}: 'categories' must be a list")
        for category in categories:
            if "id" not in category or "name" not in category:
                raise TopologyError(f"{path}: every category needs 'id' and 'name'")
        return cls(categories)

    def known_namespaces(self) -> set[str]:
        return set(self._by_namespace)

    def get(self, namespace: str) -> NamespaceInfo | None:
        return self._by_namespace.get(namespace)

    def category_of(self, namespace: str) -> str:
        info = self._by_namespace.get(namespace)
        if info:
            return info.category
        if namespace == "systemd":
            return "Core Infrastructure & Platform"
        return UNMAPPED_CATEGORY

    def describe(self, namespace: str) -> str:
        info = self._by_namespace.get(namespace)
        if info:
            return info.description
        if namespace == "systemd":
            return "Host operating system systemd journald logs & platform services."
        return ""

    def container_description(self, namespace: str, container: str) -> str:
        info = self._by_namespace.get(namespace)
        return info.container_descriptions.get(container, "") if info else ""

    def get_related(self, namespace: str) -> list[dict[str, str]]:
        """Return related namespaces with reasons and component categories."""
        info = self._by_namespace.get(namespace)
        reasons = dict(info.related_reasons) if info else dict(DEFAULT_DEPENDENCIES.get(namespace, {}))
        results = []
        for rel_ns, reason in reasons.items():
            rel_info = self._by_namespace.get(rel_ns)
            results.append({
                "namespace": rel_ns,
                "category": rel_info.category if rel_info else UNMAPPED_CATEGORY,
                "description": rel_info.description if rel_info else "",
                "reason": reason,
            })
        return results

    def categories(self) -> list[tuple[str, str]]:
        """``(id, name)`` of every category, in file order."""
        return [(c["id"], c["name"]) for c in self._categories]

    def unmapped(self, observed: Iterable[str]) -> list[str]:
        """Namespaces seen in a bundle that the topology file does not know."""
        return sorted(set(observed) - set(self._by_namespace))

