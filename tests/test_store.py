"""Unit tests for the DuckDB SessionStore."""

import tempfile
from pathlib import Path

from analyzer.parse.event import Event
from analyzer.parse.k8s_state import K8sFact
from analyzer.parse.usage import MetricFact
from analyzer.store.duck import SessionStore


def _sample_event(
    ts_micros: int,
    level: str = "INFO",
    namespace: str = "onprem",
    issue_type: str = "Info",
    message: str = "test message",
    pod: str = "pod-1",
) -> Event:
    return Event(
        ts_micros=ts_micros,
        ts_iso=f"2026-08-09T00:00:00.{ts_micros}Z",
        source="fluent",
        node="node-1",
        namespace=namespace,
        pod=pod,
        container="main",
        level=level,
        issue_type=issue_type,
        message=message,
        file="fluent.log",
        line_num=10,
    )


def test_session_store_lifecycle_and_metadata():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test_session.duckdb"
        with SessionStore(db_file) as store:
            assert store.get_metadata() is None

            # Insert events
            ev1 = _sample_event(1000, level="INFO")
            ev2 = _sample_event(2000, level="ERROR", issue_type="Timeout", message="Request timed out")
            store.insert_events([ev1, ev2])

            store.set_metadata(
                session_id="sess-123",
                filename="support_bundle.tar",
                scanned_at="2026-08-09 12:00:00 UTC",
                total_files=50,
            )

            meta = store.get_metadata()
            assert meta is not None
            assert meta["id"] == "sess-123"
            assert meta["filename"] == "support_bundle.tar"
            assert meta["min_ts_micros"] == 1000
            assert meta["max_ts_micros"] == 2000
            assert meta["total_events"] == 2
            assert meta["total_files"] == 50


def test_session_store_time_bounds_and_filtering():
    with SessionStore(":memory:") as store:
        assert store.get_time_bounds() == (None, None)

        events = [
            _sample_event(100, level="INFO", namespace="auth"),
            _sample_event(200, level="WARN", namespace="vault"),
            _sample_event(300, level="ERROR", namespace="vault", issue_type="Connection Refused"),
            _sample_event(400, level="FATAL", namespace="onprem", issue_type="OOM Killed"),
            _sample_event(500, level="ERROR", namespace="onprem", issue_type="Panic/Crash"),
        ]
        store.insert_events(events)

        assert store.get_time_bounds() == (100, 500)

        # Count by level without time filter
        lvl_counts = store.count_by_level()
        assert lvl_counts.get("INFO") == 1
        assert lvl_counts.get("WARN") == 1
        assert lvl_counts.get("ERROR") == 2
        assert lvl_counts.get("FATAL") == 1

        # Count by level with time filter [200, 400]
        bounded_lvls = store.count_by_level(from_micros=200, to_micros=400)
        assert "INFO" not in bounded_lvls
        assert bounded_lvls.get("WARN") == 1
        assert bounded_lvls.get("ERROR") == 1
        assert bounded_lvls.get("FATAL") == 1

        # Count by namespace with time filter
        ns_counts = store.count_by_namespace(from_micros=300, to_micros=500)
        assert ns_counts.get("onprem") == 2
        assert ns_counts.get("vault") == 1
        assert "auth" not in ns_counts


def test_session_store_query_events():
    with SessionStore(":memory:") as store:
        events = [
            _sample_event(100, level="INFO", namespace="ns1", message="Service started"),
            _sample_event(200, level="ERROR", namespace="ns1", issue_type="Timeout", message="DB query timeout"),
            _sample_event(300, level="ERROR", namespace="ns2", issue_type="Connection Refused", message="Redis refused"),
            _sample_event(400, level="FATAL", namespace="ns2", issue_type="OOM Killed", message="OOM killer invoked"),
        ]
        store.insert_events(events)

        # 1. Filter by level
        res_err = store.query_events(level="ERROR")
        assert len(res_err) == 2

        # 2. Filter by namespace
        res_ns1 = store.query_events(namespace="ns1")
        assert len(res_ns1) == 2

        # 3. Filter by issue_type
        res_oom = store.query_events(issue_type="OOM Killed")
        assert len(res_oom) == 1
        assert res_oom[0]["issue_type"] == "OOM Killed"

        # 4. Search text ILIKE
        res_search = store.query_events(search="redis")
        assert len(res_search) == 1
        assert res_search[0]["message"] == "Redis refused"

        # 5. Pagination
        res_page = store.query_events(limit=2, offset=1)
        assert len(res_page) == 2
        assert res_page[0]["ts_micros"] == 200
        assert res_page[1]["ts_micros"] == 300


def test_session_store_k8s_and_metrics():
    with SessionStore(":memory:") as store:
        facts = [
            K8sFact(
                ts_micros=1000,
                kind="Pod",
                namespace="onprem",
                name="api-pod/api",
                reason="OOMKilled",
                exit_code=137,
                restarts=3,
                detail="Container api OOMKilled",
            ),
            K8sFact(
                ts_micros=2000,
                kind="Pod",
                namespace="vault",
                name="vault-0/vault",
                reason="CrashLoopBackOff",
                exit_code=1,
                restarts=10,
                detail="CrashLoopBackOff",
            ),
        ]
        store.insert_k8s_facts(facts)

        k8s_all = store.get_k8s_facts()
        assert len(k8s_all) == 2

        k8s_oom = store.get_k8s_facts(reason="OOMKilled")
        assert len(k8s_oom) == 1
        assert k8s_oom[0]["exit_code"] == 137

        # Metrics
        metrics = [
            MetricFact(
                ts_micros=None,
                node="node-1",
                metric_name="disk_usage_percent",
                metric_value=92.5,
                unit="%",
                detail="Root disk 92.5%",
            )
        ]
        store.insert_metrics(metrics)

        row = store.conn.execute("SELECT node, metric_value FROM metrics").fetchone()
        assert row == ("node-1", 92.5)
