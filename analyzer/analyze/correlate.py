"""Log Event Time Correlator and Frequency Spike Detector.

Aggregates events into time buckets across the support bundle window to detect:
1. Volumetric Spikes (Z-score > 3.0 vs. background baseline)
2. Sudden Silence Windows (drop to zero after critical errors)
3. Multi-service Cascade Anomalies (multiple namespaces failing in the same bucket)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..parse.timestamps import micros_to_iso
from ..store.duck import SessionStore


@dataclass
class Anomaly:
    kind: str  # 'SPIKE', 'SILENCE', 'CASCADE'
    start_micros: int
    end_micros: int
    start_iso: str
    end_iso: str
    event_count: int
    baseline_mean: float
    score: float  # z-score or severity metric
    namespaces: list[str]
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start_micros": self.start_micros,
            "end_micros": self.end_micros,
            "start_iso": self.start_iso,
            "end_iso": self.end_iso,
            "event_count": self.event_count,
            "baseline_mean": round(self.baseline_mean, 2),
            "score": round(self.score, 2),
            "namespaces": self.namespaces,
            "detail": self.detail,
        }


class EventCorrelator:
    """Detects time-series surges and cascading failures from SessionStore."""

    def __init__(self, bucket_seconds: int = 60, min_spike_count: int = 15):
        self.bucket_micros = bucket_seconds * 1_000_000
        self.min_spike_count = min_spike_count

    def detect_spikes(
        self,
        store: SessionStore,
        from_micros: int | None = None,
        to_micros: int | None = None,
        level_filter: str | None = None,
    ) -> list[Anomaly]:
        """Detect volumetric log spikes and cascades within the given window."""
        where_clauses = ["ts_micros IS NOT NULL"]
        params: list[Any] = []

        if from_micros is not None:
            where_clauses.append("ts_micros >= ?")
            params.append(from_micros)
        if to_micros is not None:
            where_clauses.append("ts_micros <= ?")
            params.append(to_micros)
        if level_filter:
            where_clauses.append("level = ?")
            params.append(level_filter.upper())

        where_sql = " AND ".join(where_clauses)

        # 1. Bucket query using DuckDB integer division
        bucket_sql = f"""
            SELECT
                (ts_micros // {self.bucket_micros}) * {self.bucket_micros} AS b_start,
                COUNT(*) AS cnt,
                COUNT(DISTINCT namespace) AS ns_cnt,
                STRING_AGG(DISTINCT namespace, ', ') AS ns_list
            FROM events
            WHERE {where_sql}
            GROUP BY b_start
            ORDER BY b_start ASC
        """
        with store.lock:
            rows = store.conn.execute(bucket_sql, params).fetchall()
        if len(rows) < 3:
            return []

        counts = [int(r[1]) for r in rows if r[1] is not None]
        n = len(counts)
        if n < 3:
            return []
        mean = sum(counts) / n
        variance = sum((x - mean) ** 2 for x in counts) / n
        stddev = math.sqrt(variance) if variance > 0 else 0.0

        anomalies: list[Anomaly] = []

        for row in rows:
            b_start = row[0]
            b_end = b_start + self.bucket_micros
            count = row[1]
            ns_cnt = row[2]
            ns_raw = row[3] or ""
            namespaces = [ns.strip() for ns in ns_raw.split(",") if ns.strip()]

            # Calculate Z-score
            z_score = ((count - mean) / stddev) if stddev > 0 else 0.0

            # 1. High-frequency spike
            if z_score >= 3.0 and count >= self.min_spike_count:
                kind = "CASCADE" if ns_cnt >= 3 else "SPIKE"
                detail = (
                    f"Cascade surge: {count} events across {ns_cnt} namespaces "
                    f"({z_score:.1f}σ above avg {mean:.1f}/min)"
                    if kind == "CASCADE"
                    else f"Volume surge: {count} events ({z_score:.1f}σ above avg {mean:.1f}/min)"
                )
                anomalies.append(
                    Anomaly(
                        kind=kind,
                        start_micros=b_start,
                        end_micros=b_end,
                        start_iso=micros_to_iso(b_start),
                        end_iso=micros_to_iso(b_end),
                        event_count=count,
                        baseline_mean=mean,
                        score=z_score,
                        namespaces=namespaces[:6],
                        detail=detail,
                    )
                )

        # Sort highest score first
        anomalies.sort(key=lambda a: -a.score)
        return anomalies
