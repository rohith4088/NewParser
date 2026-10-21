"""Context extraction for dynamic multi-agent triage.

Queries DuckDB with high-efficiency SQL to extract concise, strictly-grounded
diagnostic telemetry per namespace and host component without flooding the LLM.
"""

from __future__ import annotations

from typing import Any

from ..analyze.correlate import EventCorrelator
from ..parse.timestamps import micros_to_iso
from ..store.duck import SessionStore
from .models import EvidenceItem


class AgentContextBuilder:
    """Builds token-efficient, factual context packages for individual agents."""

    def __init__(self, correlator: EventCorrelator | None = None):
        self.correlator = correlator or EventCorrelator(bucket_seconds=60, min_spike_count=5)

    def build_namespace_context(
        self,
        store: SessionStore,
        namespace: str,
        from_micros: int | None = None,
        to_micros: int | None = None,
        limit_logs: int = 8,
    ) -> dict[str, Any]:
        """Extracts localized telemetry for a specific Kubernetes namespace."""
        time_clauses = []
        time_params: list[Any] = [namespace]
        if from_micros is not None:
            time_clauses.append("ts_micros >= ?")
            time_params.append(from_micros)
        if to_micros is not None:
            time_clauses.append("ts_micros <= ?")
            time_params.append(to_micros)

        where_time = f" AND {' AND '.join(time_clauses)}" if time_clauses else ""

        # 1. Error counts and earliest fault timestamp
        stats_sql = f"""
            SELECT
                COUNT(*) AS total_events,
                COUNT(CASE WHEN level = 'FATAL' THEN 1 END) AS fatal_cnt,
                COUNT(CASE WHEN level = 'ERROR' THEN 1 END) AS error_cnt,
                COUNT(CASE WHEN level = 'WARN' THEN 1 END) AS warn_cnt,
                MIN(CASE WHEN level IN ('FATAL', 'ERROR') THEN ts_micros END) AS first_err_micros,
                MAX(CASE WHEN level IN ('FATAL', 'ERROR') THEN ts_micros END) AS last_err_micros,
                COUNT(DISTINCT pod) AS pod_cnt
            FROM events
            WHERE namespace = ? {where_time}
        """
        with store.lock:
            stats_row = store.conn.execute(stats_sql, time_params).fetchone()

        total_cnt = stats_row[0] or 0
        fatal_cnt = stats_row[1] or 0
        error_cnt = stats_row[2] or 0
        warn_cnt = stats_row[3] or 0
        first_err_micros = stats_row[4]
        last_err_micros = stats_row[5]
        pod_cnt = stats_row[6] or 0

        first_err_iso = micros_to_iso(first_err_micros) if first_err_micros else None
        last_err_iso = micros_to_iso(last_err_micros) if last_err_micros else None

        # 2. Extract representative evidence logs
        evidence_sql = f"""
            SELECT
                ts_micros,
                ts_iso,
                level,
                pod,
                container,
                message,
                file,
                line_num,
                issue_type
            FROM events
            WHERE namespace = ?
              AND level IN ('FATAL', 'ERROR', 'WARN')
              {where_time}
            ORDER BY
                CASE level WHEN 'FATAL' THEN 1 WHEN 'ERROR' THEN 2 ELSE 3 END ASC,
                ts_micros ASC
            LIMIT ?
        """
        ev_params = list(time_params) + [limit_logs]
        with store.lock:
            ev_rows = store.conn.execute(evidence_sql, ev_params).fetchall()

        evidence_items: list[EvidenceItem] = []
        for r in ev_rows:
            evidence_items.append(
                EvidenceItem(
                    ts_micros=r[0] or 0,
                    ts_iso=r[1] or micros_to_iso(r[0]),
                    level=r[2] or "ERROR",
                    pod=r[3],
                    container=r[4],
                    message=r[5] or "",
                    file=r[6],
                    line_num=r[7],
                    issue_type=r[8],
                    namespace=namespace,
                )
            )

        # 3. Drain templates for this namespace
        drain_sql = f"""
            SELECT
                c.cluster_id,
                c.template,
                c.size
            FROM drain_clusters c
            WHERE c.sample_namespace = ?
            ORDER BY c.size DESC
            LIMIT 4
        """
        drain_templates: list[dict[str, Any]] = []
        try:
            with store.lock:
                drain_rows = store.conn.execute(drain_sql, [namespace]).fetchall()
                for d in drain_rows:
                    drain_templates.append({"cluster_id": d[0], "template": d[1], "count": d[2]})
        except Exception:
            pass  # drain_clusters table may not be populated yet

        # 4. K8s facts (OOM, restarts, crashloops)
        facts_sql = f"""
            SELECT
                ts_micros,
                kind,
                name,
                reason,
                exit_code,
                restarts,
                detail
            FROM k8s_facts
            WHERE namespace = ?
            ORDER BY ts_micros ASC
            LIMIT 5
        """
        k8s_facts: list[dict[str, Any]] = []
        try:
            with store.lock:
                fact_rows = store.conn.execute(facts_sql, [namespace]).fetchall()
                for f in fact_rows:
                    k8s_facts.append({
                        "ts_iso": micros_to_iso(f[0]) if f[0] else None,
                        "kind": f[1],
                        "name": f[2],
                        "reason": f[3],
                        "exit_code": f[4],
                        "restarts": f[5],
                        "detail": f[6],
                    })
        except Exception:
            pass

        # 5. Spikes in this namespace
        spikes = []
        try:
            detected_spikes = self.correlator.detect_spikes(store, from_micros=from_micros, to_micros=to_micros)
            for s in detected_spikes:
                if namespace in s.namespaces:
                    spikes.append(s.to_dict())
        except Exception:
            pass

        # 6. Build prompt text
        prompt_lines = [
            f"NAMESPACE: {namespace}",
            f"EVENT SUMMARY: {total_cnt} total ({fatal_cnt} FATAL, {error_cnt} ERROR, {warn_cnt} WARN) across {pod_cnt} pods",
        ]
        if first_err_iso:
            prompt_lines.append(f"FIRST ERROR OCCURRED AT: {first_err_iso}")
        if last_err_iso:
            prompt_lines.append(f"LATEST ERROR AT: {last_err_iso}")

        if k8s_facts:
            prompt_lines.append("\nKUBERNETES CONTAINER CRASHES & LIFECYCLE FACTS:")
            for k in k8s_facts:
                ec_part = f" (Exit Code {k['exit_code']})" if k['exit_code'] is not None else ""
                rst_part = f" Restarts: {k['restarts']}" if k['restarts'] else ""
                prompt_lines.append(f"- [{k['ts_iso']}] {k['kind']} '{k['name']}': {k['reason']}{ec_part}{rst_part} {k['detail'] or ''}")

        if drain_templates:
            prompt_lines.append("\nRECURRING DRAIN ERROR PATTERNS:")
            for dt in drain_templates:
                prompt_lines.append(f"- [{dt['count']} occurrences] {dt['template']}")

        prompt_lines.append("\nPRIMARY EVIDENCE LOGS:")
        for ev in evidence_items:
            pod_part = f" [{ev.pod}]" if ev.pod else ""
            prompt_lines.append(f"- {ev.ts_iso} {ev.level}{pod_part}: {ev.message}")

        if spikes:
            prompt_lines.append(f"\nVOLUMETRIC SPIKES: {len(spikes)} surge windows detected in this namespace.")

        return {
            "namespace": namespace,
            "total_count": total_cnt,
            "fatal_count": fatal_cnt,
            "error_count": error_cnt,
            "warn_count": warn_cnt,
            "first_fault_micros": first_err_micros,
            "first_fault_iso": first_err_iso,
            "last_fault_iso": last_err_iso,
            "evidence_items": evidence_items,
            "drain_templates": drain_templates,
            "k8s_facts": k8s_facts,
            "spikes": spikes,
            "prompt_text": "\n".join(prompt_lines),
        }

    def build_host_context(
        self,
        store: SessionStore,
        unit_id: str | None = None,
        from_micros: int | None = None,
        to_micros: int | None = None,
        limit_logs: int = 8,
    ) -> dict[str, Any]:
        """Extracts localized telemetry for Host / Fluent-Bit / OS / Storage components."""
        clauses = ["(source = 'host' OR namespace IS NULL OR namespace = '')"]
        params: list[Any] = []

        if unit_id:
            clauses.append("unit_id = ?")
            params.append(unit_id)
        if from_micros is not None:
            clauses.append("ts_micros >= ?")
            params.append(from_micros)
        if to_micros is not None:
            clauses.append("ts_micros <= ?")
            params.append(to_micros)

        where_sql = " AND ".join(clauses)

        stats_sql = f"""
            SELECT
                COUNT(*) AS total_events,
                COUNT(CASE WHEN level = 'FATAL' THEN 1 END) AS fatal_cnt,
                COUNT(CASE WHEN level = 'ERROR' THEN 1 END) AS error_cnt,
                COUNT(CASE WHEN level = 'WARN' THEN 1 END) AS warn_cnt,
                MIN(CASE WHEN level IN ('FATAL', 'ERROR') THEN ts_micros END) AS first_err_micros,
                MAX(CASE WHEN level IN ('FATAL', 'ERROR') THEN ts_micros END) AS last_err_micros,
                COUNT(DISTINCT node) AS node_cnt
            FROM events
            WHERE {where_sql}
        """
        with store.lock:
            stats_row = store.conn.execute(stats_sql, params).fetchone()

        total_cnt = stats_row[0] or 0
        fatal_cnt = stats_row[1] or 0
        error_cnt = stats_row[2] or 0
        warn_cnt = stats_row[3] or 0
        first_err_micros = stats_row[4]
        last_err_micros = stats_row[5]
        node_cnt = stats_row[6] or 0

        first_err_iso = micros_to_iso(first_err_micros) if first_err_micros else None
        last_err_iso = micros_to_iso(last_err_micros) if last_err_micros else None

        evidence_sql = f"""
            SELECT
                ts_micros,
                ts_iso,
                level,
                node,
                unit_id,
                message,
                file,
                line_num,
                issue_type
            FROM events
            WHERE {where_sql}
              AND level IN ('FATAL', 'ERROR', 'WARN')
            ORDER BY
                CASE level WHEN 'FATAL' THEN 1 WHEN 'ERROR' THEN 2 ELSE 3 END ASC,
                ts_micros ASC
            LIMIT ?
        """
        ev_params = list(params) + [limit_logs]
        with store.lock:
            ev_rows = store.conn.execute(evidence_sql, ev_params).fetchall()

        evidence_items: list[EvidenceItem] = []
        for r in ev_rows:
            evidence_items.append(
                EvidenceItem(
                    ts_micros=r[0] or 0,
                    ts_iso=r[1] or micros_to_iso(r[0]),
                    level=r[2] or "ERROR",
                    pod=r[3],  # node stored in pod for host
                    container=r[4],  # unit_id
                    message=r[5] or "",
                    file=r[6],
                    line_num=r[7],
                    issue_type=r[8],
                )
            )

        unit_label = unit_id or "Host & Ingestion Pipeline"
        prompt_lines = [
            f"COMPONENT: {unit_label}",
            f"EVENT SUMMARY: {total_cnt} total ({fatal_cnt} FATAL, {error_cnt} ERROR, {warn_cnt} WARN) across {node_cnt} nodes",
        ]
        if first_err_iso:
            prompt_lines.append(f"FIRST ERROR OCCURRED AT: {first_err_iso}")
        if last_err_iso:
            prompt_lines.append(f"LATEST ERROR AT: {last_err_iso}")

        prompt_lines.append("\nPRIMARY EVIDENCE LOGS:")
        for ev in evidence_items:
            unit_part = f" [{ev.container or 'sys'}]"
            prompt_lines.append(f"- {ev.ts_iso} {ev.level}{unit_part}: {ev.message}")

        return {
            "unit_id": unit_id or "host_general",
            "total_count": total_cnt,
            "fatal_count": fatal_cnt,
            "error_count": error_cnt,
            "warn_count": warn_cnt,
            "first_fault_micros": first_err_micros,
            "first_fault_iso": first_err_iso,
            "last_fault_iso": last_err_iso,
            "evidence_items": evidence_items,
            "prompt_text": "\n".join(prompt_lines),
        }
