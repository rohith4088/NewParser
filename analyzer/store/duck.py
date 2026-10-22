"""Embedded DuckDB storage engine for support bundle sessions."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Iterable

import duckdb

from ..parse.event import Event
from ..parse.k8s_state import K8sFact
from ..parse.usage import MetricFact
from ..mine.drain import DrainParser

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class SessionStore:
    """Manages an embedded DuckDB database file for one support bundle session."""

    def __init__(self, db_path: str | Path = ":memory:", read_only: bool = False):
        self.db_path = str(db_path)
        self.read_only = read_only
        self._lock = threading.RLock()
        try:
            self.conn = duckdb.connect(self.db_path, read_only=read_only)
        except Exception as exc:
            if not read_only:
                # Fall back to read-only if another process currently holds the write lock
                self.conn = duckdb.connect(self.db_path, read_only=True)
                self.read_only = True
            else:
                raise exc

        if not self.read_only:
            self._init_schema()

    @property
    def lock(self) -> threading.RLock:
        """Lock for serializing complex multi-statement or external query sequences."""
        return self._lock

    def _init_schema(self) -> None:
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        self.conn.execute(sql)
        try:
            self.conn.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS unit_id VARCHAR")
        except Exception:
            pass
        try:
            self.conn.execute("ALTER TABLE k8s_facts ADD COLUMN IF NOT EXISTS occurrences INTEGER DEFAULT 1")
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # --- Ingestion Methods ----------------------------------------------------

    def insert_events(self, events: Iterable[Event], unit_id: str | None = None) -> int:
        """Batch insert events into DuckDB."""
        tuples = []
        for e in events:
            t = e.to_tuple()
            if len(t) == 12:
                tuples.append((*t, unit_id))
            elif len(t) == 13:
                u = unit_id if t[12] is None else t[12]
                tuples.append((*t[:12], u))
            else:
                tuples.append(t)

        if not tuples:
            return 0
        with self._lock:
            self.conn.executemany(
                """
                INSERT INTO events (
                    ts_micros, ts_iso, source, node, namespace, pod, container,
                    level, issue_type, message, file, line_num, unit_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuples,
            )
        return len(tuples)

    # --- Unit Ingestion Tracking ----------------------------------------------

    def get_parsed_units(self) -> list[dict[str, Any]]:
        """List all parsed units with status and counts."""
        try:
            with self._lock:
                rows = self.conn.execute(
                    """
                    SELECT unit_id, part_id, status, files, events, started_at, finished_at, error
                    FROM parsed_units ORDER BY finished_at ASC
                    """
                ).fetchall()
            cols = ["unit_id", "part_id", "status", "files", "events", "started_at", "finished_at", "error"]
            return [dict(zip(cols, row)) for row in rows]
        except Exception:
            return []

    def is_unit_parsed(self, unit_id: str) -> bool:
        """Check if a specific unit has been completed."""
        try:
            with self._lock:
                row = self.conn.execute(
                    "SELECT COUNT(*) FROM parsed_units WHERE unit_id = ? AND status = 'completed'",
                    [unit_id],
                ).fetchone()
            return bool(row and row[0] > 0)
        except Exception:
            return False

    def delete_unit(self, unit_id: str) -> None:
        """Remove previously parsed data for a unit before re-parsing."""
        try:
            with self._lock:
                self.conn.execute("DELETE FROM events WHERE unit_id = ?", [unit_id])
                self.conn.execute("DELETE FROM parsed_units WHERE unit_id = ?", [unit_id])
        except Exception:
            pass

    def record_unit_start(self, unit_id: str, part_id: str, started_at: str) -> None:
        """Mark unit ingestion as started."""
        try:
            with self._lock:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO parsed_units (
                        unit_id, part_id, status, files, events, started_at, finished_at, error
                    ) VALUES (?, ?, 'parsing', 0, 0, ?, NULL, NULL)
                    """,
                    [unit_id, part_id, started_at],
                )
        except Exception:
            pass

    def record_unit_complete(
        self, unit_id: str, part_id: str, files: int, events: int, finished_at: str
    ) -> None:
        """Mark unit ingestion as completed."""
        try:
            with self._lock:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO parsed_units (
                        unit_id, part_id, status, files, events, started_at, finished_at, error
                    ) VALUES (?, ?, 'completed', ?, ?, NULL, ?, NULL)
                    """,
                    [unit_id, part_id, files, events, finished_at],
                )
        except Exception:
            pass

    def record_unit_failed(
        self, unit_id: str, part_id: str, error: str, finished_at: str
    ) -> None:
        """Mark unit ingestion as failed."""
        try:
            with self._lock:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO parsed_units (
                        unit_id, part_id, status, files, events, started_at, finished_at, error
                    ) VALUES (?, ?, 'failed', 0, 0, NULL, ?, ?)
                    """,
                    [unit_id, part_id, finished_at, error],
                )
        except Exception:
            pass

    def insert_k8s_facts(self, facts: Iterable[K8sFact]) -> int:
        tuples = []
        for f in facts:
            t = f.to_tuple()
            tuples.append(t if len(t) == 9 else (*t[:8], 1))
        if not tuples:
            return 0
        with self._lock:
            self.conn.executemany(
                """
                INSERT INTO k8s_facts (
                    ts_micros, kind, namespace, name, reason, exit_code, restarts, detail, occurrences
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuples,
            )
        return len(tuples)

    def insert_metrics(self, metrics: Iterable[MetricFact]) -> int:
        tuples = [m.to_tuple() for m in metrics]
        if not tuples:
            return 0
        with self._lock:
            self.conn.executemany(
                """
                INSERT INTO metrics (
                    ts_micros, node, metric_name, metric_value, unit, detail
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                tuples,
            )
        return len(tuples)

    def set_metadata(
        self,
        session_id: str,
        filename: str,
        scanned_at: str,
        total_files: int = 0,
    ) -> None:
        with self._lock:
            # Compute min/max timestamp and event count
            row = self.conn.execute(
                "SELECT MIN(ts_micros), MAX(ts_micros), COUNT(*) FROM events"
            ).fetchone()
            min_ts = row[0] if row else None
            max_ts = row[1] if row else None
            total_events = (row[2] or 0) if row else 0

            self.conn.execute(
                """
                INSERT OR REPLACE INTO bundle_metadata (
                    id, filename, scanned_at, min_ts_micros, max_ts_micros, total_events, total_files
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [session_id, filename, scanned_at, min_ts, max_ts, total_events, total_files],
            )

    # --- Query Methods --------------------------------------------------------

    def get_metadata(self) -> dict[str, Any] | None:
        try:
            with self._lock:
                row = self.conn.execute(
                    """
                    SELECT id, filename, scanned_at, min_ts_micros, max_ts_micros, total_events, total_files
                    FROM bundle_metadata LIMIT 1
                    """
                ).fetchone()
            if row and row[0]:
                return {
                    "id": row[0],
                    "filename": row[1],
                    "scanned_at": row[2],
                    "min_ts_micros": row[3],
                    "max_ts_micros": row[4],
                    "total_events": row[5],
                    "total_files": row[6],
                }
        except Exception:
            pass

        # Fallback: compute live stats while ingestion is actively in progress
        try:
            with self._lock:
                row_ev = self.conn.execute(
                    "SELECT MIN(ts_micros), MAX(ts_micros), COUNT(*) FROM events"
                ).fetchone()
            if row_ev and row_ev[2] > 0:
                stem = Path(self.db_path).stem
                return {
                    "id": stem,
                    "filename": f"{stem}.tar",
                    "scanned_at": "Parsing in progress...",
                    "min_ts_micros": row_ev[0],
                    "max_ts_micros": row_ev[1],
                    "total_events": row_ev[2],
                    "total_files": 0,
                }
        except Exception:
            pass
        return None

    def get_time_bounds(self) -> tuple[int | None, int | None]:
        with self._lock:
            row = self.conn.execute("SELECT MIN(ts_micros), MAX(ts_micros) FROM events").fetchone()
        if not row:
            return None, None
        return row[0], row[1]

    def count_by_level(
        self, from_micros: int | None = None, to_micros: int | None = None
    ) -> dict[str, int]:
        query = "SELECT level, COUNT(*) FROM events WHERE 1=1"
        params: list[Any] = []
        if from_micros is not None:
            query += " AND ts_micros >= ?"
            params.append(from_micros)
        if to_micros is not None:
            query += " AND ts_micros <= ?"
            params.append(to_micros)
        query += " GROUP BY level"
        with self._lock:
            rows = self.conn.execute(query, params).fetchall()
        return {str(row[0]): int(row[1]) for row in rows if row[0] is not None}

    def count_by_origin(
        self, from_micros: int | None = None, to_micros: int | None = None
    ) -> dict[str, int]:
        """Return event counts split between cluster microservice logs and host snapshot logs."""
        query = """
            SELECT 
                CASE 
                    WHEN unit_id LIKE 'cluster:%' THEN 'cluster'
                    WHEN unit_id LIKE 'host%' THEN 'host'
                    WHEN source IN ('journal', 'syslog') THEN 'host'
                    ELSE 'cluster'
                END AS origin,
                COUNT(*)
            FROM events
            WHERE 1=1
        """
        params: list[Any] = []
        if from_micros is not None:
            query += " AND ts_micros >= ?"
            params.append(from_micros)
        if to_micros is not None:
            query += " AND ts_micros <= ?"
            params.append(to_micros)
        query += " GROUP BY origin"
        with self._lock:
            rows = self.conn.execute(query, params).fetchall()
        counts = {"cluster": 0, "host": 0}
        for r in rows:
            if r[0] in counts:
                counts[r[0]] = int(r[1])
        return counts

    def count_by_namespace(
        self, from_micros: int | None = None, to_micros: int | None = None, origin: str | None = None
    ) -> dict[str, int]:
        query = "SELECT namespace, COUNT(*) FROM events WHERE 1=1"
        params: list[Any] = []
        if from_micros is not None:
            query += " AND ts_micros >= ?"
            params.append(from_micros)
        if to_micros is not None:
            query += " AND ts_micros <= ?"
            params.append(to_micros)
        if origin and origin != "all":
            if origin.lower() == "cluster":
                query += " AND (unit_id LIKE 'cluster:%' OR (unit_id IS NULL AND source NOT IN ('journal', 'syslog')))"
            elif origin.lower() in ("host", "hostsnapshot"):
                query += " AND (unit_id LIKE 'host%' OR source IN ('journal', 'syslog'))"
        query += " GROUP BY namespace ORDER BY COUNT(*) DESC"
        with self._lock:
            rows = self.conn.execute(query, params).fetchall()
        return {str(row[0]): int(row[1]) for row in rows if row[0] is not None}

    def query_events(
        self,
        from_micros: int | None = None,
        to_micros: int | None = None,
        origin: str | None = None,
        level: str | None = None,
        namespace: str | None = None,
        issue_type: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = "SELECT ts_micros, ts_iso, source, node, namespace, pod, container, level, issue_type, message, file, line_num, unit_id FROM events WHERE 1=1"
        params: list[Any] = []

        if from_micros is not None:
            query += " AND ts_micros >= ?"
            params.append(from_micros)
        if to_micros is not None:
            query += " AND ts_micros <= ?"
            params.append(to_micros)
        if origin and origin != "all":
            if origin.lower() == "cluster":
                query += " AND (unit_id LIKE 'cluster:%' OR (unit_id IS NULL AND source NOT IN ('journal', 'syslog')))"
            elif origin.lower() in ("host", "hostsnapshot"):
                query += " AND (unit_id LIKE 'host%' OR source IN ('journal', 'syslog'))"
        if level and level != "all":
            query += " AND level = ?"
            params.append(level.upper())
        if namespace and namespace != "all":
            query += " AND namespace = ?"
            params.append(namespace)
        if issue_type and issue_type != "all":
            query += " AND issue_type = ?"
            params.append(issue_type)
        if search:
            query += " AND (message ILIKE ? OR pod ILIKE ? OR container ILIKE ?)"
            s = f"%{search}%"
            params.extend([s, s, s])

        query += " ORDER BY ts_micros ASC NULLS LAST LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._lock:
            rows = self.conn.execute(query, params).fetchall()

        cols = [
            "ts_micros", "ts_iso", "source", "node", "namespace", "pod", "container",
            "level", "issue_type", "message", "file", "line_num", "unit_id"
        ]
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            uid = d.get("unit_id") or ""
            src = d.get("source") or ""
            if uid.startswith("cluster:") or (not uid and src not in ("journal", "syslog")):
                d["origin"] = "cluster"
                d["origin_label"] = "Cluster Logs"
            else:
                d["origin"] = "host"
                d["origin_label"] = f"Host Snapshot ({d.get('node') or 'host'})"
            results.append(d)
        return results

    def get_k8s_facts(
        self, reason: str | None = None, namespace: str | None = None
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT ts_micros, kind, namespace, name, reason, exit_code, restarts, detail, "
            "COALESCE(occurrences, 1) FROM k8s_facts WHERE 1=1"
        )
        params: list[Any] = []
        if reason:
            query += " AND reason = ?"
            params.append(reason)
        if namespace and namespace != "all":
            query += " AND namespace = ?"
            params.append(namespace)
        query += " ORDER BY ts_micros ASC NULLS LAST"

        with self._lock:
            rows = self.conn.execute(query, params).fetchall()
        cols = ["ts_micros", "kind", "namespace", "name", "reason", "exit_code", "restarts", "detail", "occurrences"]
        return [dict(zip(cols, row)) for row in rows]

    def get_timeline_data(
        self, start_micros: int, end_micros: int, bucket_size: int
    ) -> list[tuple[Any, Any, Any]]:
        sql = f"""
            SELECT
                ((ts_micros - {start_micros}) // {bucket_size}) AS b_idx,
                level,
                COUNT(*)
            FROM events
            WHERE ts_micros >= {start_micros} AND ts_micros <= {end_micros}
            GROUP BY b_idx, level
            ORDER BY b_idx ASC
        """
        with self._lock:
            return self.conn.execute(sql).fetchall()

    def get_patient_zero(
        self,
        namespace: str | None = None,
        from_micros: int | None = None,
        to_micros: int | None = None,
    ) -> dict[str, Any] | None:
        """Find the earliest high-severity error event (Patient Zero) in scope."""
        clauses = ["level IN ('FATAL', 'CRITICAL', 'ERROR')"]
        params: list[Any] = []

        if namespace and namespace != "all":
            clauses.append("namespace = ?")
            params.append(namespace)
        if from_micros is not None:
            clauses.append("ts_micros >= ?")
            params.append(from_micros)
        if to_micros is not None:
            clauses.append("ts_micros <= ?")
            params.append(to_micros)

        sql = f"""
            SELECT ts_iso, ts_micros, namespace, pod, container, level, issue_type,
                   SUBSTRING(message, 1, 280) as msg
            FROM events
            WHERE {' AND '.join(clauses)}
            ORDER BY ts_micros ASC
            LIMIT 1
        """
        with self._lock:
            try:
                row = self.conn.execute(sql, params).fetchone()
            except Exception:
                row = None

        if not row:
            return None

        return {
            "ts_iso": row[0],
            "ts_micros": row[1],
            "namespace": row[2],
            "pod": row[3],
            "container": row[4],
            "level": row[5],
            "issue_type": row[6] or "General Failure",
            "message": " ".join(str(row[7]).split()),
        }

    def get_mined_templates(
        self,
        namespace: str | None = None,
        from_micros: int | None = None,
        to_micros: int | None = None,
        limit: int = 5,
        sample_size: int = 300,
    ) -> list[dict[str, Any]]:
        """Mine and aggregate recurring failure patterns using the Drain algorithm."""
        clauses = ["level IN ('FATAL', 'CRITICAL', 'ERROR', 'WARN')"]
        params: list[Any] = []

        if namespace and namespace != "all":
            clauses.append("namespace = ?")
            params.append(namespace)
        if from_micros is not None:
            clauses.append("ts_micros >= ?")
            params.append(from_micros)
        if to_micros is not None:
            clauses.append("ts_micros <= ?")
            params.append(to_micros)

        sql = f"""
            SELECT ts_iso, namespace, pod, message
            FROM events
            WHERE {' AND '.join(clauses)}
            ORDER BY ts_micros DESC
            LIMIT {sample_size}
        """
        with self._lock:
            try:
                sample_rows = self.conn.execute(sql, params).fetchall()
            except Exception:
                sample_rows = []

        if not sample_rows:
            return []

        drain = DrainParser(max_depth=4, sim_th=0.5, max_clusters=150)
        cluster_meta: dict[int, dict[str, Any]] = {}

        for ts_iso, ns, pod, msg in sample_rows:
            if not msg:
                continue
            c = drain.match(msg)
            if c.cluster_id not in cluster_meta:
                cluster_meta[c.cluster_id] = {
                    "namespace": ns,
                    "pod": pod,
                    "first_ts": ts_iso,
                    "last_ts": ts_iso,
                }
            else:
                cluster_meta[c.cluster_id]["first_ts"] = ts_iso  # earliest seen since sampled DESC

        top_clusters = drain.top_templates(limit=limit)
        results = []
        for c in top_clusters:
            meta = cluster_meta.get(c.cluster_id, {})
            results.append({
                "cluster_id": c.cluster_id,
                "template": c.template,
                "count": c.size,
                "sample": c.sample,
                "namespace": meta.get("namespace", "unknown"),
                "pod": meta.get("pod", "unknown"),
                "first_ts": meta.get("first_ts", ""),
                "last_ts": meta.get("last_ts", ""),
            })
        return results

    def get_scored_events(
        self,
        user_query: str = "",
        namespace: str | None = None,
        from_micros: int | None = None,
        to_micros: int | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        """Retrieve relevant log events ranked by lexical and diagnostic relevance."""
        stopwords = {
            "what", "when", "where", "which", "could", "would", "should", "there", "their",
            "about", "error", "errors", "logs", "show", "tell", "check", "with", "from",
            "have", "this", "that", "these", "those", "does", "been", "being", "into",
            "more", "some", "such", "than", "then", "very", "were", "will", "your", "explain"
        }
        raw_tokens = [w.strip("?,.:;!'\"()[]{}").lower() for w in user_query.split()]
        keywords = [w for w in raw_tokens if len(w) >= 2 and w not in stopwords]

        score_parts = [
            "CASE level WHEN 'FATAL' THEN 4.0 WHEN 'CRITICAL' THEN 3.5 WHEN 'ERROR' THEN 2.0 WHEN 'WARN' THEN 1.0 ELSE 0.5 END",
            "CASE WHEN issue_type != 'Other' AND issue_type != 'General' AND issue_type IS NOT NULL THEN 2.0 ELSE 0.0 END",
        ]
        params: list[Any] = []
        where_clauses = ["level IN ('FATAL', 'CRITICAL', 'ERROR', 'WARN')"]

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
            for kw in keywords[:6]:
                p = f"%{kw}%"
                score_parts.append(
                    "(CASE WHEN pod ILIKE ? THEN 3.5 ELSE 0 END + "
                    "CASE WHEN namespace ILIKE ? THEN 3.0 ELSE 0 END + "
                    "CASE WHEN issue_type ILIKE ? THEN 2.5 ELSE 0 END + "
                    "CASE WHEN message ILIKE ? THEN 2.0 ELSE 0 END)"
                )
                params.extend([p, p, p, p])

            kw_matches = []
            for kw in keywords[:6]:
                p = f"%{kw}%"
                kw_matches.append("(message ILIKE ? OR pod ILIKE ? OR namespace ILIKE ? OR issue_type ILIKE ?)")
                params.extend([p, p, p, p])
            where_clauses.append(f"({' OR '.join(kw_matches)})")

        total_score_sql = " + ".join(score_parts)
        where_sql = " AND ".join(where_clauses)

        query_sql = f"""
            SELECT ts_iso, namespace, pod, container, level, issue_type,
                   SUBSTRING(message, 1, 260) as msg,
                   ({total_score_sql}) as relevance_score
            FROM events
            WHERE {where_sql}
            ORDER BY relevance_score DESC, ts_micros DESC
            LIMIT {limit}
        """

        with self._lock:
            try:
                rows = self.conn.execute(query_sql, params).fetchall()
            except Exception:
                rows = []

            # If no keyword matches found, fall back to recent high-severity events
            if not rows and keywords:
                fallback_params: list[Any] = []
                fb_clauses = ["level IN ('FATAL', 'CRITICAL', 'ERROR')"]
                if namespace and namespace != "all":
                    fb_clauses.append("namespace = ?")
                    fallback_params.append(namespace)
                if from_micros is not None:
                    fb_clauses.append("ts_micros >= ?")
                    fallback_params.append(from_micros)
                if to_micros is not None:
                    fb_clauses.append("ts_micros <= ?")
                    fallback_params.append(to_micros)

                fallback_sql = f"""
                    SELECT ts_iso, namespace, pod, container, level, issue_type,
                           SUBSTRING(message, 1, 260) as msg,
                           3.0 as relevance_score
                    FROM events
                    WHERE {' AND '.join(fb_clauses)}
                    ORDER BY ts_micros DESC
                    LIMIT {limit}
                """
                try:
                    rows = self.conn.execute(fallback_sql, fallback_params).fetchall()
                except Exception:
                    rows = []

        results = []
        for ts_iso, ns, pod, container, lvl, itype, msg, score in rows:
            results.append({
                "ts_iso": ts_iso,
                "namespace": ns,
                "pod": pod,
                "container": container,
                "level": lvl,
                "issue_type": itype or "General",
                "message": " ".join(str(msg).split()),
                "relevance_score": round(float(score), 2),
            })
        return results

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

            # 2. Patient Zero Candidate (Earliest Failure in Scope)
            patient_zero = self.get_patient_zero(namespace=namespace, from_micros=from_micros, to_micros=to_micros)
            if patient_zero:
                c_tag = f"/{patient_zero['container']}" if patient_zero['container'] else ""
                lines.append("\n### Patient Zero Candidate (Earliest Failure in Scope):")
                lines.append(f"- Timestamp: {patient_zero['ts_iso']}")
                lines.append(f"- Culprit Component: [{patient_zero['namespace']}] {patient_zero['pod']}{c_tag}")
                lines.append(f"- Level & Classification: [{patient_zero['level']}] {patient_zero['issue_type']}")
                lines.append(f"- Initial Error: {patient_zero['message']}")

            # 3. Deterministic Incident Signatures (Rule Findings)
            if rule_findings:
                lines.append("\n### Deterministic Incident Signatures (High Confidence):")
                for f in rule_findings[:5]:
                    rem_str = f" | Remediation: {f.remediation[:80]}..." if getattr(f, "remediation", None) else ""
                    desc = getattr(f, "description", "")[:110]
                    lines.append(f"- [{getattr(f, 'severity', 'HIGH')}] {getattr(f, 'title', 'Incident')} ({getattr(f, 'count', 1)} occurrences): {desc}{rem_str}")

            # 4. Mined Log Event Patterns (Drain Template Mining)
            mined_templates = self.get_mined_templates(namespace=namespace, from_micros=from_micros, to_micros=to_micros, limit=4)
            if mined_templates:
                lines.append("\n### Dominant Failure Patterns (Drain Template Mining):")
                for idx, t in enumerate(mined_templates, 1):
                    t_first = t["first_ts"].split("T")[-1] if "T" in t["first_ts"] else t["first_ts"]
                    t_last = t["last_ts"].split("T")[-1] if "T" in t["last_ts"] else t["last_ts"]
                    time_span = f" (Period: {t_first} to {t_last})" if t_first and t_last and t_first != t_last else ""
                    lines.append(f"- Pattern #{idx} [{t['count']}x events across {t['namespace']}/{t['pod']}]{time_span}: `{t['template'][:160]}`")

            # 5. Top Failing Services
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

            # 6. K8s Pod Failures / Restarts (k8s_facts)
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

            # 7. Relevant Log Events matching User Query / Filter (BM25/Lexical Scored)
            matched_events = self.get_scored_events(
                user_query=user_query,
                namespace=namespace,
                from_micros=from_micros,
                to_micros=to_micros,
                limit=limit,
            )
            if matched_events:
                lines.append(f"\n### Relevant Live Log Events (Scored Relevance Retrieval, {len(matched_events)} events):")
                for ev in matched_events:
                    ts_short = ev["ts_iso"].split("T")[-1] if "T" in str(ev["ts_iso"]) else str(ev["ts_iso"])
                    c_tag = f"/{ev['container']}" if ev['container'] else ""
                    lines.append(f"- [{ts_short}] [{ev['level']}] [{ev['namespace']}/{ev['pod']}{c_tag}] (Score: {ev['relevance_score']}, {ev['issue_type']}): {ev['message']}")

        return "\n".join(lines)

