"""Deterministic YAML-based Detection Rules Engine.

Loads declarative rule specifications from ``rules/*.yaml`` and executes them
against a DuckDB SessionStore to detect known failure signatures (e.g., OOM kills,
expired TLS certs, connection refused, storage exhaustion).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..parse.timestamps import micros_to_iso
from ..store.duck import SessionStore

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "INFO": 4,
}


@dataclass(frozen=True)
class Rule:
    """A declarative detection rule loaded from YAML."""

    id: str
    title: str
    severity: str
    category: str
    description: str
    remediation: str
    target: str  # 'events', 'k8s_facts', or 'metrics'
    condition: str
    requires: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        required_keys = ("id", "title", "severity", "target", "condition")
        for k in required_keys:
            if not data.get(k):
                raise ValueError(f"Rule missing required field: {k!r}")

        return cls(
            id=str(data["id"]).strip(),
            title=str(data["title"]).strip(),
            severity=str(data.get("severity", "MEDIUM")).strip().upper(),
            category=str(data.get("category", "General")).strip(),
            description=str(data.get("description", "")).strip(),
            remediation=str(data.get("remediation", "")).strip(),
            target=str(data["target"]).strip().lower(),
            condition=str(data["condition"]).strip(),
            requires=list(data.get("requires") or []),
        )


@dataclass
class Finding:
    """Result of a triggered detection rule."""

    rule_id: str
    title: str
    severity: str
    category: str
    description: str
    remediation: str
    count: int
    first_seen_micros: int | None
    last_seen_micros: int | None
    first_seen_iso: str | None
    last_seen_iso: str | None
    namespaces: list[str]
    nodes: list[str]
    samples: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "severity": self.severity,
            "category": self.category,
            "description": self.description,
            "remediation": self.remediation,
            "count": self.count,
            "first_seen_micros": self.first_seen_micros,
            "last_seen_micros": self.last_seen_micros,
            "first_seen_iso": self.first_seen_iso,
            "last_seen_iso": self.last_seen_iso,
            "namespaces": self.namespaces,
            "nodes": self.nodes,
            "samples": self.samples,
        }


class RuleEngine:
    """Manages rule loading and evaluation against SessionStore."""

    def __init__(self, rules_dir: Path | str | None = None):
        self.rules_dir = Path(rules_dir) if rules_dir else None
        self.rules: list[Rule] = []
        if self.rules_dir and self.rules_dir.exists():
            self.load_rules()

    def load_rules(self) -> int:
        """Load all YAML rule files from rules_dir."""
        if not self.rules_dir or not self.rules_dir.is_dir():
            return 0
        self.rules = []
        for path in sorted(self.rules_dir.glob("*.yaml")) + sorted(self.rules_dir.glob("*.yml")):
            try:
                content = path.read_text(encoding="utf-8")
                data = yaml.safe_load(content)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            self.rules.append(Rule.from_dict(item))
                elif isinstance(data, dict):
                    self.rules.append(Rule.from_dict(data))
            except Exception as exc:
                logger.warning("Failed to load rule from %s: %s", path, exc)
        return len(self.rules)

    def evaluate(
        self,
        store: SessionStore,
        from_micros: int | None = None,
        to_micros: int | None = None,
    ) -> list[Finding]:
        """Evaluate loaded rules against DuckDB store with optional time filter."""
        findings: list[Finding] = []

        with store.lock:
            for rule in self.rules:
                finding = self._evaluate_rule(rule, store, from_micros, to_micros)
                if finding:
                    findings.append(finding)

        # Sort findings by severity (CRITICAL first), then count DESC
        findings.sort(
            key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), -f.count)
        )
        return findings

    def _evaluate_rule(
        self,
        rule: Rule,
        store: SessionStore,
        from_micros: int | None,
        to_micros: int | None,
    ) -> Finding | None:
        target_table = rule.target
        if target_table not in ("events", "k8s_facts", "metrics"):
            return None

        # Build query
        time_clauses = []
        params: list[Any] = []

        # k8s_facts and metrics are largely point-in-time snapshots taken when
        # the bundle was collected and often carry no timestamp. ``NULL >= x``
        # is false in SQL, so without the IS NULL escape every snapshot row
        # would silently vanish as soon as a time window is applied.
        keep_undated = target_table in ("k8s_facts", "metrics")
        if from_micros is not None:
            time_clauses.append("(ts_micros IS NULL OR ts_micros >= ?)" if keep_undated else "ts_micros >= ?")
            params.append(from_micros)
        if to_micros is not None:
            time_clauses.append("(ts_micros IS NULL OR ts_micros <= ?)" if keep_undated else "ts_micros <= ?")
            params.append(to_micros)

        where_expr = f"({rule.condition})"
        if time_clauses:
            where_expr += f" AND {' AND '.join(time_clauses)}"

        # A single aggregated k8s Event may represent many occurrences.
        count_expr = "COALESCE(SUM(COALESCE(occurrences, 1)), 0)" if target_table == "k8s_facts" else "COUNT(*)"

        try:
            # 1. Get count and time bounds
            agg_sql = f"""
                SELECT
                    {count_expr},
                    MIN(ts_micros),
                    MAX(ts_micros)
                FROM {target_table}
                WHERE {where_expr}
            """
            row = store.conn.execute(agg_sql, params).fetchone()
            if not row or not row[0]:
                return None

            count = int(row[0])
            first_ts = row[1]
            last_ts = row[2]

            # 2. Get distinct namespaces and nodes if columns exist
            namespaces: list[str] = []
            nodes: list[str] = []

            if target_table in ("events", "k8s_facts"):
                ns_rows = store.conn.execute(
                    f"SELECT DISTINCT namespace FROM {target_table} WHERE {where_expr} AND namespace IS NOT NULL LIMIT 20",
                    params,
                ).fetchall()
                namespaces = sorted([str(r[0]) for r in ns_rows if r[0] is not None])

            if target_table in ("events", "metrics"):
                node_rows = store.conn.execute(
                    f"SELECT DISTINCT node FROM {target_table} WHERE {where_expr} AND node IS NOT NULL LIMIT 20",
                    params,
                ).fetchall()
                nodes = sorted([str(r[0]) for r in node_rows if r[0] is not None])

            # 3. Fetch up to 5 sample evidence records
            sample_sql = f"""
                SELECT *
                FROM {target_table}
                WHERE {where_expr}
                ORDER BY ts_micros DESC NULLS LAST
                LIMIT 5
            """
            cursor = store.conn.execute(sample_sql, params)
            col_names = [d[0] for d in (cursor.description or []) if d and len(d) > 0]
            sample_rows = cursor.fetchall()
            samples = [dict(zip(col_names, sr)) for sr in sample_rows]

            return Finding(
                rule_id=rule.id,
                title=rule.title,
                severity=rule.severity,
                category=rule.category,
                description=rule.description,
                remediation=rule.remediation,
                count=count,
                first_seen_micros=first_ts,
                last_seen_micros=last_ts,
                first_seen_iso=micros_to_iso(first_ts) if first_ts else None,
                last_seen_iso=micros_to_iso(last_ts) if last_ts else None,
                namespaces=namespaces,
                nodes=nodes,
                samples=samples,
            )
        except Exception as exc:
            logger.warning("Error evaluating rule %s: %s", rule.id, exc)
            return None
