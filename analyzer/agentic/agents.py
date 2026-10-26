"""Specialized agents for Hierarchical Multi-Agent Triage (H-MAT).

Includes BaseAgent with dual-mode execution (Local LLM Qwen 2.5:3B with
deterministic fallback), NamespaceSubAgent, HostSubAgent, Domain Leads,
and the Incident Commander.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Any

from ..parse.timestamps import micros_to_iso
from .models import (
    AgentRole,
    AgentStatus,
    AgentVerdict,
    EvidenceItem,
    TimelineStep,
    TriageReport,
)

logger = logging.getLogger(__name__)


_CACHED_OLLAMA_URL: str | None = None
_OLLAMA_CHECKED: bool = False


def _find_ollama_url() -> str | None:
    """Detect available Ollama host with caching and fast local priority."""
    global _CACHED_OLLAMA_URL, _OLLAMA_CHECKED
    if _OLLAMA_CHECKED:
        return _CACHED_OLLAMA_URL

    candidates = [
        os.environ.get("OLLAMA_HOST"),
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://host.docker.internal:11434",
    ]
    for url in candidates:
        if not url:
            continue
        if not url.startswith("http"):
            url = f"http://{url}"
        try:
            req = urllib.request.Request(f"{url}/api/tags", headers={"User-Agent": "OnpremAgentic"})
            with urllib.request.urlopen(req, timeout=0.3) as resp:
                if resp.status == 200:
                    _CACHED_OLLAMA_URL = url
                    _OLLAMA_CHECKED = True
                    return url
        except Exception:
            continue

    _OLLAMA_CHECKED = True
    _CACHED_OLLAMA_URL = None
    return None


class BaseAgent:
    """Base class for all triage agents providing LLM communication and grounded fallbacks."""

    def __init__(
        self,
        agent_id: str,
        title: str,
        role: AgentRole,
        target: str,
        model: str = "qwen2.5:3b",
        ollama_url: str | None = None,
        use_llm: bool = True,
    ):
        self.agent_id = agent_id
        self.title = title
        self.role = role
        self.target = target
        self.model = model
        self.ollama_url = ollama_url
        self.use_llm = use_llm

    def _query_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout: float = 8.0,
    ) -> dict[str, Any] | None:
        """Attempts local LLM inference via Ollama with JSON structure."""
        if not self.use_llm:
            return None

        url = self.ollama_url or _find_ollama_url()
        if not url:
            return None

        num_ctx = int(os.environ.get("ANALYZER_AI_NUM_CTX", 8192 if any(k in self.model for k in ("14b", "27b", "32b", "70b")) else 4096))
        payload = {
            "model": self.model,
            "format": "json",
            "stream": False,
            "options": {
                "num_ctx": num_ctx,
                "temperature": 0.1,  # Low temperature for factual, deterministic analysis
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        try:
            req = urllib.request.Request(
                f"{url}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    content_str = data.get("message", {}).get("content", "").strip()
                    if content_str:
                        return json.loads(content_str)
        except Exception as exc:
            logger.debug(f"LLM query failed for {self.agent_id}, using deterministic fallback: {exc}")
        return None


class NamespaceSubAgent(BaseAgent):
    """Specialist sub-agent analyzing an individual Kubernetes namespace."""

    def __init__(
        self,
        namespace: str,
        model: str = "qwen2.5:3b",
        ollama_url: str | None = None,
        use_llm: bool = True,
    ):
        super().__init__(
            agent_id=f"ns_{namespace}",
            title=f"Namespace Sub-Agent: {namespace}",
            role=AgentRole.SUB_NAMESPACE,
            target=namespace,
            model=model,
            ollama_url=ollama_url,
            use_llm=use_llm,
        )

    def analyze(self, ctx: dict[str, Any]) -> AgentVerdict:
        start_t = time.perf_counter()
        total_err = ctx.get("fatal_count", 0) + ctx.get("error_count", 0)
        warn_cnt = ctx.get("warn_count", 0)

        # Baseline Status Evaluation
        if ctx.get("fatal_count", 0) > 0 or ctx.get("error_count", 0) >= 5:
            status = AgentStatus.CRITICAL
        elif total_err > 0 or warn_cnt >= 10:
            status = AgentStatus.DEGRADED
        else:
            status = AgentStatus.HEALTHY

        evidence_items: list[EvidenceItem] = ctx.get("evidence_items", [])
        first_iso = ctx.get("first_fault_iso")
        first_micros = ctx.get("first_fault_micros")

        # 1. Deterministic default extraction from evidence and k8s facts
        k8s_facts = ctx.get("k8s_facts", [])
        drain_templates = ctx.get("drain_templates", [])

        primary_issue = ""
        causal_hypothesis = ""

        if k8s_facts:
            top_k8s = k8s_facts[0]
            reason = top_k8s.get("reason", "Pod Crash")
            ec = f" (exit code {top_k8s['exit_code']})" if top_k8s.get("exit_code") is not None else ""
            primary_issue = f"{top_k8s['kind']} '{top_k8s['name']}': {reason}{ec}"
            causal_hypothesis = f"Kubernetes lifecycle failure observed on {top_k8s['name']}. {top_k8s.get('detail') or ''}"
        elif evidence_items:
            first_ev = evidence_items[0]
            primary_issue = f"{first_ev.issue_type or 'Error'}: {first_ev.message[:120]}"
            causal_hypothesis = f"Recurring fault in {first_ev.pod or 'pods'}. Primary log: {first_ev.message[:160]}"
        elif drain_templates:
            top_dt = drain_templates[0]
            primary_issue = f"Pattern: {top_dt['template'][:120]}"
            causal_hypothesis = f"Recurring error cluster observed {top_dt['count']} times."
        else:
            primary_issue = "No significant errors or faults recorded in this namespace."
            causal_hypothesis = "Component operated within expected operational parameters."

        # 2. Local LLM Enhancement (if Ollama is active)
        system_prompt = (
            "You are an on-prem Kubernetes Namespace Diagnostic Specialist.\n"
            "Analyze the namespace telemetry and output a strict JSON object with fields:\n"
            '{"status": "CRITICAL" | "DEGRADED" | "HEALTHY", "primary_issue": string, "causal_hypothesis": string, "confidence": float}'
        )
        llm_res = self._query_llm(system_prompt, ctx.get("prompt_text", ""))
        if llm_res and isinstance(llm_res, dict):
            if llm_res.get("status") in ["CRITICAL", "DEGRADED", "HEALTHY"]:
                status = AgentStatus(llm_res["status"].lower())
            if llm_res.get("primary_issue"):
                primary_issue = str(llm_res["primary_issue"])
            if llm_res.get("causal_hypothesis"):
                causal_hypothesis = str(llm_res["causal_hypothesis"])

        duration_ms = int((time.perf_counter() - start_t) * 1000)

        return AgentVerdict(
            agent_id=self.agent_id,
            title=self.title,
            role=self.role,
            status=status,
            target=self.target,
            earliest_fault_micros=first_micros,
            earliest_fault_iso=first_iso,
            error_count=total_err,
            primary_issue=primary_issue,
            causal_hypothesis=causal_hypothesis,
            evidence=evidence_items[:6],
            confidence=0.95 if total_err > 0 else 1.0,
            duration_ms=duration_ms,
        )


class HostSubAgent(BaseAgent):
    """Specialist sub-agent analyzing host snapshot, storage, and ingestion logs."""

    def __init__(
        self,
        unit_id: str | None = None,
        model: str = "qwen2.5:3b",
        ollama_url: str | None = None,
        use_llm: bool = True,
    ):
        target_name = unit_id or "system_journald"
        super().__init__(
            agent_id=f"host_{target_name}",
            title=f"Host Sub-Agent: {target_name}",
            role=AgentRole.SUB_HOST,
            target=target_name,
            model=model,
            ollama_url=ollama_url,
            use_llm=use_llm,
        )

    def analyze(self, ctx: dict[str, Any]) -> AgentVerdict:
        start_t = time.perf_counter()
        total_err = ctx.get("fatal_count", 0) + ctx.get("error_count", 0)
        warn_cnt = ctx.get("warn_count", 0)

        if ctx.get("fatal_count", 0) > 0 or ctx.get("error_count", 0) >= 3:
            status = AgentStatus.CRITICAL
        elif total_err > 0 or warn_cnt >= 5:
            status = AgentStatus.DEGRADED
        else:
            status = AgentStatus.HEALTHY

        evidence_items: list[EvidenceItem] = ctx.get("evidence_items", [])
        first_iso = ctx.get("first_fault_iso")
        first_micros = ctx.get("first_fault_micros")

        if evidence_items:
            first_ev = evidence_items[0]
            primary_issue = f"{first_ev.issue_type or 'Host Log'}: {first_ev.message[:120]}"
            causal_hypothesis = f"Host level failure in {self.target}. Log snippet: {first_ev.message[:160]}"
        else:
            primary_issue = "Host operating system and ingestion daemons healthy."
            causal_hypothesis = "No underlying node-level or storage hardware panics detected."

        duration_ms = int((time.perf_counter() - start_t) * 1000)

        return AgentVerdict(
            agent_id=self.agent_id,
            title=self.title,
            role=self.role,
            status=status,
            target=self.target,
            earliest_fault_micros=first_micros,
            earliest_fault_iso=first_iso,
            error_count=total_err,
            primary_issue=primary_issue,
            causal_hypothesis=causal_hypothesis,
            evidence=evidence_items[:6],
            confidence=0.95,
            duration_ms=duration_ms,
        )


class ClusterDomainAgent(BaseAgent):
    """Domain Lead synthesizing all Kubernetes namespace sub-agent verdicts."""

    def __init__(
        self,
        model: str = "qwen2.5:3b",
        ollama_url: str | None = None,
        use_llm: bool = True,
    ):
        super().__init__(
            agent_id="lead_cluster",
            title="Cluster Domain Lead",
            role=AgentRole.DOMAIN_CLUSTER,
            target="Kubernetes Cluster",
            model=model,
            ollama_url=ollama_url,
            use_llm=use_llm,
        )

    def synthesize(self, namespace_verdicts: list[AgentVerdict]) -> AgentVerdict:
        start_t = time.perf_counter()
        critical_v = [v for v in namespace_verdicts if v.status == AgentStatus.CRITICAL]
        degraded_v = [v for v in namespace_verdicts if v.status == AgentStatus.DEGRADED]

        if critical_v:
            status = AgentStatus.CRITICAL
        elif degraded_v:
            status = AgentStatus.DEGRADED
        else:
            status = AgentStatus.HEALTHY

        # Sort failing namespaces chronologically to find the earliest cluster fault
        failing_sorted = sorted(
            [v for v in namespace_verdicts if v.earliest_fault_micros is not None and v.status in (AgentStatus.CRITICAL, AgentStatus.DEGRADED)],
            key=lambda v: v.earliest_fault_micros or 0,
        )

        total_errors = sum(v.error_count for v in namespace_verdicts)
        earliest_ns = failing_sorted[0] if failing_sorted else None
        earliest_micros = earliest_ns.earliest_fault_micros if earliest_ns else None
        earliest_iso = earliest_ns.earliest_fault_iso if earliest_ns else None

        if earliest_ns:
            cascade_names = [v.target for v in failing_sorted]
            primary_issue = f"Initiated in namespace '{earliest_ns.target}': {earliest_ns.primary_issue}"
            causal_hypothesis = (
                f"Cluster failure progression initiated in '{earliest_ns.target}' at {earliest_iso}, "
                f"cascading across {len(cascade_names)} namespaces ({', '.join(cascade_names[:4])})."
            )
        else:
            primary_issue = "All Kubernetes microservices and namespaces operating nominally."
            causal_hypothesis = "No cluster-level cascading failures detected."

        duration_ms = int((time.perf_counter() - start_t) * 1000)

        return AgentVerdict(
            agent_id=self.agent_id,
            title=self.title,
            role=self.role,
            status=status,
            target=self.target,
            earliest_fault_micros=earliest_micros,
            earliest_fault_iso=earliest_iso,
            error_count=total_errors,
            primary_issue=primary_issue,
            causal_hypothesis=causal_hypothesis,
            evidence=[e for v in failing_sorted for e in v.evidence][:8],
            confidence=0.98,
            duration_ms=duration_ms,
        )


class HostDomainAgent(BaseAgent):
    """Domain Lead synthesizing host OS, storage hardware, and Fluent-Bit verdicts."""

    def __init__(
        self,
        model: str = "qwen2.5:3b",
        ollama_url: str | None = None,
        use_llm: bool = True,
    ):
        super().__init__(
            agent_id="lead_host",
            title="Host & Storage Lead",
            role=AgentRole.DOMAIN_HOST,
            target="Host Snapshot & Ingestion",
            model=model,
            ollama_url=ollama_url,
            use_llm=use_llm,
        )

    def synthesize(self, host_verdicts: list[AgentVerdict]) -> AgentVerdict:
        start_t = time.perf_counter()
        critical_v = [v for v in host_verdicts if v.status == AgentStatus.CRITICAL]
        degraded_v = [v for v in host_verdicts if v.status == AgentStatus.DEGRADED]

        if critical_v:
            status = AgentStatus.CRITICAL
        elif degraded_v:
            status = AgentStatus.DEGRADED
        else:
            status = AgentStatus.HEALTHY

        failing_sorted = sorted(
            [v for v in host_verdicts if v.earliest_fault_micros is not None and v.status in (AgentStatus.CRITICAL, AgentStatus.DEGRADED)],
            key=lambda v: v.earliest_fault_micros or 0,
        )

        total_errors = sum(v.error_count for v in host_verdicts)
        earliest_unit = failing_sorted[0] if failing_sorted else None
        earliest_micros = earliest_unit.earliest_fault_micros if earliest_unit else None
        earliest_iso = earliest_unit.earliest_fault_iso if earliest_unit else None

        if earliest_unit:
            primary_issue = f"Host fault in '{earliest_unit.target}': {earliest_unit.primary_issue}"
            causal_hypothesis = f"Host or storage driver anomaly emerged at {earliest_iso} in unit '{earliest_unit.target}'."
        else:
            primary_issue = "Host operating system, node storage, and fluent-bit pipelines healthy."
            causal_hypothesis = "No host-level physical drive, CSI detachment, or kernel panic observed."

        duration_ms = int((time.perf_counter() - start_t) * 1000)

        return AgentVerdict(
            agent_id=self.agent_id,
            title=self.title,
            role=self.role,
            status=status,
            target=self.target,
            earliest_fault_micros=earliest_micros,
            earliest_fault_iso=earliest_iso,
            error_count=total_errors,
            primary_issue=primary_issue,
            causal_hypothesis=causal_hypothesis,
            evidence=[e for v in failing_sorted for e in v.evidence][:8],
            confidence=0.98,
            duration_ms=duration_ms,
        )


class IncidentCommanderAgent(BaseAgent):
    """L3 Commander Agent synthesizing all domain verdicts to pinpoint Patient Zero."""

    def __init__(
        self,
        model: str = "qwen2.5:3b",
        ollama_url: str | None = None,
        use_llm: bool = True,
    ):
        super().__init__(
            agent_id="commander",
            title="Incident Commander Agent",
            role=AgentRole.COMMANDER,
            target="Support Bundle Incident",
            model=model,
            ollama_url=ollama_url,
            use_llm=use_llm,
        )

    def formulate_report(
        self,
        session_id: str,
        cluster_lead_verdict: AgentVerdict,
        host_lead_verdict: AgentVerdict,
        all_verdicts: list[AgentVerdict],
    ) -> TriageReport:
        start_t = time.perf_counter()

        overall_status = AgentStatus.HEALTHY
        if cluster_lead_verdict.status == AgentStatus.CRITICAL or host_lead_verdict.status == AgentStatus.CRITICAL:
            overall_status = AgentStatus.CRITICAL
        elif cluster_lead_verdict.status == AgentStatus.DEGRADED or host_lead_verdict.status == AgentStatus.DEGRADED:
            overall_status = AgentStatus.DEGRADED

        # Determine Patient Zero: The absolute earliest critical/degraded fault across ALL sub-agents
        candidates = [
            v for v in all_verdicts
            if v.earliest_fault_micros is not None and v.status in (AgentStatus.CRITICAL, AgentStatus.DEGRADED)
            and v.role in (AgentRole.SUB_NAMESPACE, AgentRole.SUB_HOST)
        ]
        candidates.sort(key=lambda v: v.earliest_fault_micros or 0)

        patient_zero_agent = None
        patient_zero_component = None
        patient_zero_iso = None
        patient_zero_micros = None

        if candidates:
            pz = candidates[0]
            patient_zero_agent = pz.agent_id
            patient_zero_component = pz.target
            patient_zero_iso = pz.earliest_fault_iso
            patient_zero_micros = pz.earliest_fault_micros

        # Build chronological Causal Chain
        causal_chain: list[TimelineStep] = []
        for i, c in enumerate(candidates):
            domain_label = "Kubernetes" if c.role == AgentRole.SUB_NAMESPACE else "Host"
            causal_chain.append(
                TimelineStep(
                    timestamp_iso=c.earliest_fault_iso or "",
                    ts_micros=c.earliest_fault_micros or 0,
                    component=c.target,
                    domain=domain_label,
                    event_type=c.status.value.upper(),
                    description=c.primary_issue,
                    is_patient_zero=(i == 0),
                )
            )

        # Generate actionable remediation commands based on Patient Zero & failing components
        remediation_playbook: list[str] = []
        if patient_zero_component:
            remediation_playbook.append(f"# Step 1: Inspect Patient Zero component '{patient_zero_component}'")
            if any(c.role == AgentRole.SUB_NAMESPACE for c in candidates if c.target == patient_zero_component):
                remediation_playbook.append(f"kubectl -n {patient_zero_component} get pods -o wide")
                remediation_playbook.append(f"kubectl -n {patient_zero_component} describe pods --sort-by='.status.startTime'")
                remediation_playbook.append(f"kubectl -n {patient_zero_component} logs -l app={patient_zero_component} --previous --tail=100")
            else:
                remediation_playbook.append(f"systemctl status {patient_zero_component}")
                remediation_playbook.append(f"journalctl -u {patient_zero_component} -n 100 --no-pager")

        for c in candidates[1:3]:
            remediation_playbook.append(f"# Step: Verify downstream cascading component '{c.target}'")
            if c.role == AgentRole.SUB_NAMESPACE:
                remediation_playbook.append(f"kubectl -n {c.target} get events --sort-by='.metadata.creationTimestamp'")
            else:
                remediation_playbook.append(f"journalctl -xe | grep -i '{c.target}' | tail -n 50")

        if not remediation_playbook:
            remediation_playbook.append("# No active incidents found. Cluster and host health are nominal.")

        # Executive Summary
        if patient_zero_component:
            summary = (
                f"Incident investigation completed. The root cause (Patient Zero) originated in "
                f"'{patient_zero_component}' at {patient_zero_iso}. This triggered a cascading degradation "
                f"across {len(candidates)} total monitored services. "
                f"Primary root fault: {candidates[0].primary_issue}"
            )
        else:
            summary = "Incident investigation completed. No critical service outages or log anomalies detected across cluster namespaces and host snapshot."

        total_duration = int((time.perf_counter() - start_t) * 1000)

        # Append Domain leads and Commander to verdict list
        commander_verdict = AgentVerdict(
            agent_id=self.agent_id,
            title=self.title,
            role=self.role,
            status=overall_status,
            target=self.target,
            earliest_fault_micros=patient_zero_micros,
            earliest_fault_iso=patient_zero_iso,
            error_count=sum(c.error_count for c in candidates),
            primary_issue=summary,
            causal_hypothesis="Synthesized root-cause causal graph.",
            confidence=0.99,
            duration_ms=total_duration,
        )

        all_final_verdicts = list(all_verdicts)
        all_final_verdicts.append(cluster_lead_verdict)
        all_final_verdicts.append(host_lead_verdict)
        all_final_verdicts.append(commander_verdict)

        return TriageReport(
            session_id=session_id,
            generated_at=micros_to_iso(int(time.time() * 1_000_000)),
            overall_status=overall_status,
            patient_zero_agent=patient_zero_agent,
            patient_zero_component=patient_zero_component,
            patient_zero_iso=patient_zero_iso,
            patient_zero_micros=patient_zero_micros,
            executive_summary=summary,
            causal_chain=causal_chain,
            remediation_playbook=remediation_playbook,
            agent_verdicts=all_final_verdicts,
            total_duration_ms=total_duration,
        )
