"""Hierarchical Multi-Agent Triage (H-MAT) Framework for onprem-analyzer.

Enables dynamic, swarm-style root-cause analysis across Kubernetes namespaces,
host telemetry, and storage subsystems using local Qwen 2.5:3B intelligence.
"""

from .agents import (
    BaseAgent,
    ClusterDomainAgent,
    HostDomainAgent,
    HostSubAgent,
    IncidentCommanderAgent,
    NamespaceSubAgent,
)
from .context_builder import AgentContextBuilder
from .models import (
    AgentRole,
    AgentStatus,
    AgentVerdict,
    EvidenceItem,
    TimelineStep,
    TriageReport,
)
from .orchestrator import AgenticTriageOrchestrator

__all__ = [
    "AgentRole",
    "AgentStatus",
    "AgentVerdict",
    "EvidenceItem",
    "TimelineStep",
    "TriageReport",
    "AgentContextBuilder",
    "BaseAgent",
    "NamespaceSubAgent",
    "HostSubAgent",
    "ClusterDomainAgent",
    "HostDomainAgent",
    "IncidentCommanderAgent",
    "AgenticTriageOrchestrator",
]
