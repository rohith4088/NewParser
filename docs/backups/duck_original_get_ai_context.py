# Original get_ai_context from analyzer/store/duck.py (Prior to Point 4 enhancements)

    def get_ai_context(
        self,
        user_query: str = "",
        namespace: str | None = None,
        from_micros: int | None = None,
        to_micros: int | None = None,
        limit: int = 12,
        rule_findings: list[Any] | None = None,
    ) -> str:
        """Extract high-density, structured dynamic log context for local AI."""
        lines: list[str] = []

        with self._lock:
            # 1. Total overview & level breakdown
            meta = self.get_metadata() or {}
            levels = self.count_by_level(from_micros=from_micros, to_micros=to_micros)
            total_events = sum(levels.values())
            error_count = levels.get("ERROR", 0) + levels.get("FATAL", 0) + levels.get("CRITICAL", 0)

            lines.append("### Active Bundle Overview")
            lines.append(f"- Session ID: {meta.get('id', 'unknown')}")
            lines.append(f"- Total Events: {total_events:,}")
            lines.append(f"- Error/Fatal Count: {error_count:,} (Errors: {levels.get('ERROR', 0):,}, Warnings: {levels.get('WARN', 0):,})")

            # 2. Deterministic Incident Signatures (Rule Findings)
            if rule_findings:
                lines.append("\n### Deterministic Incident Signatures (High Confidence):")
                for f in rule_findings[:5]:
                    rem_str = f" | Remediation: {f.remediation[:80]}..." if getattr(f, "remediation", None) else ""
                    desc = getattr(f, "description", "")[:110]
                    lines.append(f"- [{getattr(f, 'severity', 'HIGH')}] {getattr(f, 'title', 'Incident')} ({getattr(f, 'count', 1)} occurrences): {desc}{rem_str}")

            # 2. Top Failing Services
            try:
                top_failing = self.conn.execute(
                    """
                    SELECT namespace, pod, COUNT(*) as cnt
                    FROM events
                    WHERE level IN ('ERROR', 'FATAL', 'CRITICAL')
                    GROUP BY namespace, pod
                    ORDER BY cnt DESC
                    LIMIT 5
                    """
                ).fetchall()
                if top_failing:
                    lines.append("\n### Top Services with Errors:")
                    for ns, pod, cnt in top_failing:
                        lines.append(f"- [{ns}] {pod}: {cnt:,} errors")
            except Exception:
                pass

            # 3. K8s Pod Failures / Restarts (k8s_facts)
            try:
                k8s_anomalies = self.conn.execute(
                    """
                    SELECT namespace, name, reason, exit_code, restarts, detail
                    FROM k8s_facts
                    WHERE restarts > 0 OR (exit_code IS NOT NULL AND exit_code != 0) OR reason NOT IN ('Running', 'Completed')
                    ORDER BY restarts DESC NULLS LAST
                    LIMIT 5
                    """
                ).fetchall()
                if k8s_anomalies:
                    lines.append("\n### Kubernetes Pod Anomalies & Restarts:")
                    for ns, name, reason, exit_code, restarts, detail in k8s_anomalies:
                        detail_str = f" - {detail[:70]}" if detail else ""
                        lines.append(f"- [{ns}] {name}: state={reason}, restarts={restarts}, exit_code={exit_code}{detail_str}")
            except Exception:
                pass

            # 4. Relevant Log Events matching User Query / Filter
            stopwords = {"what", "when", "where", "which", "could", "would", "should", "there", "their", "about", "error", "errors", "logs", "show", "tell", "check", "with", "from", "have", "this", "that"}
            raw_tokens = [w.strip("?,.:;!'\"()[]{}").lower() for w in user_query.split()]
            keywords = [w for w in raw_tokens if len(w) >= 3 and w not in stopwords]

            where_clauses = ["level IN ('ERROR', 'FATAL', 'CRITICAL', 'WARN')"]
            params: list[Any] = []

            if namespace and namespace != "all":
                where_clauses.append("namespace = ?")
                params.append(namespace)

            if from_micros is not None:
                where_clauses.append("ts_micros >= ?")
                params.append(from_micros)
            if to_micros is not None:
                where_clauses.append("ts_micros <= ?")
                params.append(to_micros)

            if keywords:
                kw_conditions = []
                for kw in keywords[:4]:
                    kw_conditions.append("(message ILIKE ? OR pod ILIKE ? OR namespace ILIKE ?)")
                    params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])
                where_clauses.append(f"({' OR '.join(kw_conditions)})")

            where_str = " AND ".join(where_clauses)
            query_sql = f"""
                SELECT ts_iso, namespace, pod, container, level, SUBSTRING(message, 1, 240) as msg
                FROM events
                WHERE {where_str}
                ORDER BY ts_micros DESC
                LIMIT {limit}
            """
            try:
                matched_events = self.conn.execute(query_sql, params).fetchall()
            except Exception:
                matched_events = []

            # If no events matched with keywords, fall back to recent high-severity errors
            if not matched_events and keywords:
                try:
                    fallback_sql = f"""
                        SELECT ts_iso, namespace, pod, container, level, SUBSTRING(message, 1, 240) as msg
                        FROM events
                        WHERE level IN ('ERROR', 'FATAL', 'CRITICAL')
                        ORDER BY ts_micros DESC
                        LIMIT {limit}
                    """
                    matched_events = self.conn.execute(fallback_sql).fetchall()
                except Exception:
                    matched_events = []

            if matched_events:
                lines.append(f"\n### Relevant Live Log Events ({len(matched_events)} events):")
                for ts_iso, ns, pod, container, lvl, msg in matched_events:
                    ts_short = ts_iso.split("T")[-1] if "T" in str(ts_iso) else str(ts_iso)
                    c_tag = f"/{container}" if container else ""
                    clean_msg = " ".join(str(msg).split())
                    lines.append(f"- [{ts_short}] [{lvl}] [{ns}/{pod}{c_tag}]: {clean_msg}")

        return "\n".join(lines)
