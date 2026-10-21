"""Data models for the Hierarchical Multi-Agent Triage (H-MAT) Framework.

Defines roles, statuses, evidence models, individual agent verdicts,
and the synthesized final incident triage report.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class AgentRole(str, Enum):
    """Hierarchy roles in the multi-agent incident triage tree."""

    COMMANDER = "commander"
    DOMAIN_CLUSTER = "cluster_lead"
    DOMAIN_HOST = "host_lead"
    SUB_NAMESPACE = "namespace_subagent"
    SUB_HOST = "host_subagent"


class AgentStatus(str, Enum):
    """Diagnostic status determined by an agent."""

    IDLE = "idle"
    RUNNING = "running"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    ERROR = "error"


@dataclass
class EvidenceItem:
    """A concrete, grounded log event or fact supporting an agent's conclusion."""

    ts_iso: str
    ts_micros: int
    level: str
    message: str
    namespace: str | None = None
    pod: str | None = None
    container: str | None = None
    file: str | None = None
    line_num: int | None = None
    issue_type: str | None = None
    drain_cluster_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentVerdict:
    """The structured diagnosis produced by a single agent."""

    agent_id: str
    title: str
    role: AgentRole
    status: AgentStatus
    target: str  # namespace name, host unit, or domain title
    earliest_fault_micros: int | None = None
    earliest_fault_iso: str | None = None
    error_count: int = 0
    primary_issue: str = ""
    causal_hypothesis: str = ""
    evidence: list[EvidenceItem] = field(default_factory=list)
    confidence: float = 1.0  # 0.0 to 1.0
    raw_analysis: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        d["status"] = self.status.value
        d["evidence"] = [e.to_dict() if isinstance(e, EvidenceItem) else e for e in self.evidence]
        return d


@dataclass
class TimelineStep:
    """A point along the chronological cascade from Patient Zero to downstream outages."""

    timestamp_iso: str
    ts_micros: int
    component: str
    domain: str
    event_type: str
    description: str
    is_patient_zero: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TriageReport:
    """The finalized incident report synthesized by the Incident Commander Agent."""

    session_id: str
    generated_at: str
    overall_status: AgentStatus
    patient_zero_agent: str | None = None
    patient_zero_component: str | None = None
    patient_zero_iso: str | None = None
    patient_zero_micros: int | None = None
    executive_summary: str = ""
    causal_chain: list[TimelineStep] = field(default_factory=list)
    remediation_playbook: list[str] = field(default_factory=list)
    agent_verdicts: list[AgentVerdict] = field(default_factory=list)
    total_duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "generated_at": self.generated_at,
            "overall_status": self.overall_status.value,
            "patient_zero_agent": self.patient_zero_agent,
            "patient_zero_component": self.patient_zero_component,
            "patient_zero_iso": self.patient_zero_iso,
            "patient_zero_micros": self.patient_zero_micros,
            "executive_summary": self.executive_summary,
            "causal_chain": [s.to_dict() if isinstance(s, TimelineStep) else s for s in self.causal_chain],
            "remediation_playbook": self.remediation_playbook,
            "agent_verdicts": [v.to_dict() if isinstance(v, AgentVerdict) else v for v in self.agent_verdicts],
            "total_duration_ms": self.total_duration_ms,
        }
