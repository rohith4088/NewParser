"""Multi-Agent Orchestrator for Support Bundle Triage.

Dynamically discovers namespaces and host units from DuckDB, dynamically
spawns specialized agents, coordinates sequential/queued execution to respect
hardware limits (8GB RAM / Qwen 2.5:3B), and yields live progress events.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Generator

from ..store.duck import SessionStore
from .agents import (
    ClusterDomainAgent,
    HostDomainAgent,
    HostSubAgent,
    IncidentCommanderAgent,
    NamespaceSubAgent,
)
from .context_builder import AgentContextBuilder
from .models import AgentStatus, AgentVerdict, TriageReport

logger = logging.getLogger(__name__)


class AgenticTriageOrchestrator:
    """Coordinates dynamic multi-agent discovery, execution, and synthesis."""

    def __init__(
        self,
        store: SessionStore,
        model: str = "qwen2.5:3b",
        ollama_url: str | None = None,
        use_llm: bool = True,
    ):
        self.store = store
        self.model = model
        self.ollama_url = ollama_url
        self.use_llm = use_llm
        self.context_builder = AgentContextBuilder()
        self._cached_report: TriageReport | None = None

    def discover_targets(
        self,
        from_micros: int | None = None,
        to_micros: int | None = None,
    ) -> dict[str, list[str]]:
        """Dynamically identifies active namespaces and host log units from the DuckDB store."""
        time_clauses = []
        time_params: list[Any] = []
        if from_micros is not None:
            time_clauses.append("ts_micros >= ?")
            time_params.append(from_micros)
        if to_micros is not None:
            time_clauses.append("ts_micros <= ?")
            time_params.append(to_micros)

        where_time = f" AND {' AND '.join(time_clauses)}" if time_clauses else ""

        # 1. Namespaces with events
        ns_sql = f"""
            SELECT namespace, COUNT(*) as cnt,
                   COUNT(CASE WHEN level IN ('FATAL', 'ERROR') THEN 1 END) as err_cnt
            FROM events
            WHERE namespace IS NOT NULL AND namespace != '' {where_time}
            GROUP BY namespace
            ORDER BY err_cnt DESC, cnt DESC
        """
        with self.store.lock:
            ns_rows = self.store.conn.execute(ns_sql, time_params).fetchall()
        namespaces = [r[0] for r in ns_rows if r[0]]

        # 2. Host units
        host_sql = f"""
            SELECT DISTINCT unit_id
            FROM events
            WHERE (source = 'host' OR namespace IS NULL OR namespace = '')
              AND unit_id IS NOT NULL AND unit_id != ''
              {where_time}
            LIMIT 6
        """
        with self.store.lock:
            host_rows = self.store.conn.execute(host_sql, time_params).fetchall()
        host_units = [r[0] for r in host_rows if r[0]]
        if not host_units:
            # Check if there are general host logs
            cnt_host_sql = f"""
                SELECT COUNT(*) FROM events
                WHERE (source = 'host' OR namespace IS NULL OR namespace = '')
                {where_time}
            """
            with self.store.lock:
                h_cnt = self.store.conn.execute(cnt_host_sql, time_params).fetchone()[0]
            if h_cnt > 0:
                host_units = ["host_snapshot"]

        return {
            "namespaces": namespaces,
            "host_units": host_units,
        }

    def run_stream(
        self,
        session_id: str,
        from_micros: int | None = None,
        to_micros: int | None = None,
    ) -> Generator[dict[str, Any], None, TriageReport]:
        """Runs the multi-agent triage stream, yielding live SSE progress events."""
        start_time = time.perf_counter()

        # Step 1: Dynamic Discovery
        targets = self.discover_targets(from_micros, to_micros)
        namespaces = targets["namespaces"]
        host_units = targets["host_units"]

        tree_info = {
            "type": "tree_discovered",
            "cluster_namespaces": namespaces,
            "host_units": host_units,
            "total_agents": len(namespaces) + len(host_units) + 3,  # sub-agents + 2 domain leads + commander
        }
        yield tree_info

        # Step 2: Execute Kubernetes Namespace Sub-Agents
        namespace_verdicts: list[AgentVerdict] = []
        for ns in namespaces:
            agent = NamespaceSubAgent(ns, model=self.model, ollama_url=self.ollama_url, use_llm=self.use_llm)
            yield {
                "type": "agent_started",
                "agent_id": agent.agent_id,
                "title": agent.title,
                "role": agent.role.value,
                "target": ns,
            }

            ctx = self.context_builder.build_namespace_context(
                self.store,
                namespace=ns,
                from_micros=from_micros,
                to_micros=to_micros,
            )
            verdict = agent.analyze(ctx)
            namespace_verdicts.append(verdict)

            yield {
                "type": "agent_completed",
                "agent_id": agent.agent_id,
                "verdict": verdict.to_dict(),
            }

        # Step 3: Execute Host / Fluent-Bit Sub-Agents
        host_verdicts: list[AgentVerdict] = []
        for hu in host_units:
            agent = HostSubAgent(hu, model=self.model, ollama_url=self.ollama_url, use_llm=self.use_llm)
            yield {
                "type": "agent_started",
                "agent_id": agent.agent_id,
                "title": agent.title,
                "role": agent.role.value,
                "target": hu,
            }

            ctx = self.context_builder.build_host_context(
                self.store,
                unit_id=hu,
                from_micros=from_micros,
                to_micros=to_micros,
            )
            verdict = agent.analyze(ctx)
            host_verdicts.append(verdict)

            yield {
                "type": "agent_completed",
                "agent_id": agent.agent_id,
                "verdict": verdict.to_dict(),
            }

        # Step 4: Cluster Domain Lead Synthesis
        cluster_lead = ClusterDomainAgent(model=self.model, ollama_url=self.ollama_url, use_llm=self.use_llm)
        yield {
            "type": "agent_started",
            "agent_id": cluster_lead.agent_id,
            "title": cluster_lead.title,
            "role": cluster_lead.role.value,
            "target": cluster_lead.target,
        }
        cluster_verdict = cluster_lead.synthesize(namespace_verdicts)
        yield {
            "type": "agent_completed",
            "agent_id": cluster_lead.agent_id,
            "verdict": cluster_verdict.to_dict(),
        }

        # Step 5: Host Domain Lead Synthesis
        host_lead = HostDomainAgent(model=self.model, ollama_url=self.ollama_url, use_llm=self.use_llm)
        yield {
            "type": "agent_started",
            "agent_id": host_lead.agent_id,
            "title": host_lead.title,
            "role": host_lead.role.value,
            "target": host_lead.target,
        }
        host_verdict = host_lead.synthesize(host_verdicts)
        yield {
            "type": "agent_completed",
            "agent_id": host_lead.agent_id,
            "verdict": host_verdict.to_dict(),
        }

        # Step 6: Incident Commander Synthesis (Pinpointing Patient Zero)
        commander = IncidentCommanderAgent(model=self.model, ollama_url=self.ollama_url, use_llm=self.use_llm)
        yield {
            "type": "agent_started",
            "agent_id": commander.agent_id,
            "title": commander.title,
            "role": commander.role.value,
            "target": commander.target,
        }

        all_sub_verdicts = namespace_verdicts + host_verdicts
        report = commander.formulate_report(
            session_id=session_id,
            cluster_lead_verdict=cluster_verdict,
            host_lead_verdict=host_verdict,
            all_verdicts=all_sub_verdicts,
        )
        report.total_duration_ms = int((time.perf_counter() - start_time) * 1000)
        self._cached_report = report

        yield {
            "type": "triage_completed",
            "report": report.to_dict(),
        }

        return report

    def run(
        self,
        session_id: str,
        from_micros: int | None = None,
        to_micros: int | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> TriageReport:
        """Synchronous run executing the generator and notifying callbacks."""
        final_report = None
        for event in self.run_stream(session_id, from_micros, to_micros):
            if on_progress:
                on_progress(event)
            if event.get("type") == "triage_completed":
                # Convert back to TriageReport if needed, or use cached
                final_report = self._cached_report
        return final_report or self._cached_report
