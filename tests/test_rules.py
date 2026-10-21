"""Unit tests for the YAML Detection Rule Engine."""

import tempfile
from pathlib import Path

from analyzer.analyze.rules import Finding, Rule, RuleEngine
from analyzer.parse.event import Event
from analyzer.parse.k8s_state import K8sFact
from analyzer.parse.usage import MetricFact
from analyzer.store.duck import SessionStore


def test_rule_loading_and_validation():
    with tempfile.TemporaryDirectory() as tmpdir:
        rules_dir = Path(tmpdir)
        rule_yaml = """
- id: TEST_RULE_1
  title: Test Rule 1
  severity: CRITICAL
  category: Security
  target: events
  condition: issue_type = 'OOM Killed'
  remediation: Check memory

- id: TEST_RULE_2
  title: Test Rule 2
  severity: LOW
  target: k8s_facts
  condition: reason = 'CrashLoopBackOff'
"""
        (rules_dir / "test.yaml").write_text(rule_yaml, encoding="utf-8")

        engine = RuleEngine(rules_dir)
        assert len(engine.rules) == 2
        r1 = engine.rules[0]
        assert r1.id == "TEST_RULE_1"
        assert r1.severity == "CRITICAL"
        assert r1.target == "events"
        assert r1.category == "Security"


def test_rule_evaluation_and_ordering():
    with tempfile.TemporaryDirectory() as tmpdir:
        rules_dir = Path(tmpdir)
        rule_yaml = """
- id: LOW_ERR
  title: Low severity test
  severity: LOW
  target: events
  condition: level = 'WARN'

- id: CRITICAL_OOM
  title: Critical OOM
  severity: CRITICAL
  category: Core Infrastructure
  remediation: Increase RAM
  target: events
  condition: issue_type = 'OOM Killed'

- id: HIGH_DISK
  title: High disk usage
  severity: HIGH
  target: metrics
  condition: metric_name = 'disk_usage_percent' AND metric_value >= 80.0
"""
        (rules_dir / "rules.yaml").write_text(rule_yaml, encoding="utf-8")
        engine = RuleEngine(rules_dir)

        with SessionStore(":memory:") as store:
            events = [
                Event(
                    ts_micros=100,
                    ts_iso="2026-08-09T00:00:00.000100Z",
                    source="fluent",
                    node="node-1",
                    namespace="vault",
                    pod="vault-0",
                    container="vault",
                    level="WARN",
                    issue_type="Warning",
                    message="Minor warning",
                    file="test.log",
                    line_num=1,
                ),
                Event(
                    ts_micros=200,
                    ts_iso="2026-08-09T00:00:00.000200Z",
                    source="syslog",
                    node="node-1",
                    namespace="onprem",
                    pod="api-pod",
                    container="api",
                    level="FATAL",
                    issue_type="OOM Killed",
                    message="Killed process 1234 (api) score 900",
                    file="test.log",
                    line_num=2,
                ),
            ]
            store.insert_events(events)

            metrics = [
                MetricFact(
                    ts_micros=300,
                    node="node-2",
                    metric_name="disk_usage_percent",
                    metric_value=88.5,
                    unit="%",
                    detail="Disk 88.5% used",
                )
            ]
            store.insert_metrics(metrics)

            findings = engine.evaluate(store)
            assert len(findings) == 3

            # Ordering must be CRITICAL first, then HIGH, then LOW
            assert findings[0].rule_id == "CRITICAL_OOM"
            assert findings[0].severity == "CRITICAL"
            assert findings[0].count == 1
            assert findings[0].namespaces == ["onprem"]
            assert findings[0].remediation == "Increase RAM"

            assert findings[1].rule_id == "HIGH_DISK"
            assert findings[1].severity == "HIGH"
            assert findings[1].nodes == ["node-2"]

            assert findings[2].rule_id == "LOW_ERR"
            assert findings[2].severity == "LOW"


def test_rule_evaluation_time_bounded():
    with tempfile.TemporaryDirectory() as tmpdir:
        rules_dir = Path(tmpdir)
        rule_yaml = """
- id: OOM_EVENT
  title: OOM Event
  severity: CRITICAL
  target: events
  condition: issue_type = 'OOM Killed'
"""
        (rules_dir / "rules.yaml").write_text(rule_yaml, encoding="utf-8")
        engine = RuleEngine(rules_dir)

        with SessionStore(":memory:") as store:
            store.insert_events([
                Event(
                    ts_micros=1000,
                    ts_iso="iso",
                    source="fluent",
                    node="n1",
                    namespace="ns",
                    pod="p",
                    container="c",
                    level="ERROR",
                    issue_type="OOM Killed",
                    message="OOM 1",
                    file="f",
                    line_num=1,
                ),
                Event(
                    ts_micros=5000,
                    ts_iso="iso",
                    source="fluent",
                    node="n1",
                    namespace="ns",
                    pod="p",
                    container="c",
                    level="ERROR",
                    issue_type="OOM Killed",
                    message="OOM 2",
                    file="f",
                    line_num=2,
                ),
            ])

            # Filter window before second event
            findings_window = engine.evaluate(store, from_micros=500, to_micros=2000)
            assert len(findings_window) == 1
            assert findings_window[0].count == 1
            assert findings_window[0].first_seen_micros == 1000

            # Filter window where no event occurred
            findings_empty = engine.evaluate(store, from_micros=6000, to_micros=7000)
            assert len(findings_empty) == 0


def test_load_bundled_production_rules():
    # Verify that the seeded rules in onprem-analyzer/rules load without errors
    rules_path = Path(__file__).resolve().parents[1] / "rules"
    engine = RuleEngine(rules_path)
    assert len(engine.rules) >= 6
    ids = {r.id for r in engine.rules}
    assert "K8S_OOM_KILLED_POD" in ids
    assert "VAULT_CONNECTION_REFUSED" in ids
    assert "HOST_DISK_PRESSURE_CRITICAL" in ids
    assert "TLS_CERTIFICATE_EXPIRED" in ids
