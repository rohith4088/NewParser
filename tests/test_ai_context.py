"""Unit tests for Supercharged Local AI Copilot RAG & Context Generation."""

import pytest
from analyzer.parse.event import Event
from analyzer.parse.k8s_state import K8sFact
from analyzer.store.duck import SessionStore


@pytest.fixture
def populated_store():
    """Create an in-memory SessionStore with realistic multi-service failure events."""
    store = SessionStore(":memory:")
    
    # Bundle metadata
    store.set_metadata("session_test_001", "support_bundle.tar", "2026-10-18T10:00:00Z", total_files=10)

    # Simulated cascade:
    # 1. 10:00:01 - Vault fails first (Patient Zero)
    # 2. 10:00:02 - Vault repeats connection refused
    # 3. 10:00:03 - Vault repeats connection refused
    # 4. 10:00:04 - CoreDNS SERVFAIL starts
    # 5. 10:00:05 - dscc-console-ui gets OOMKilled
    events = [
        Event(
            ts_micros=1000000,
            ts_iso="2026-10-18T10:00:01Z",
            source="fluent",
            node="node-1",
            namespace="auth",
            pod="vault-0",
            container="vault",
            level="FATAL",
            issue_type="Panic/Crash",
            message="fatal error: dial tcp 10.0.0.1:8200: connect: connection refused",
            file="vault.log",
            line_num=1,
        ),
        Event(
            ts_micros=2000000,
            ts_iso="2026-10-18T10:00:02Z",
            source="fluent",
            node="node-1",
            namespace="auth",
            pod="vault-0",
            container="vault",
            level="ERROR",
            issue_type="Connection Refused",
            message="dial tcp 10.0.0.1:8200: connect: connection refused",
            file="vault.log",
            line_num=2,
        ),
        Event(
            ts_micros=3000000,
            ts_iso="2026-10-18T10:00:03Z",
            source="fluent",
            node="node-1",
            namespace="auth",
            pod="vault-0",
            container="vault",
            level="ERROR",
            issue_type="Connection Refused",
            message="dial tcp 10.0.0.2:8200: connect: connection refused",
            file="vault.log",
            line_num=3,
        ),
        Event(
            ts_micros=4000000,
            ts_iso="2026-10-18T10:00:04Z",
            source="fluent",
            node="node-2",
            namespace="kube-system",
            pod="coredns-787d4945fb-2k9b4",
            container="coredns",
            level="ERROR",
            issue_type="General",
            message="[WARNING] plugin/errors: 2 auth.vault.svc.cluster.local. A: SERVFAIL",
            file="coredns.log",
            line_num=45,
        ),
        Event(
            ts_micros=5000000,
            ts_iso="2026-10-18T10:00:05Z",
            source="fluent",
            node="node-2",
            namespace="onprem",
            pod="dscc-console-ui-7cf6485746-fm29t",
            container="ui",
            level="FATAL",
            issue_type="OOM Killed",
            message="Process killed: out of memory (limit: 512MiB, used: 524MiB)",
            file="ui.log",
            line_num=112,
        ),
        Event(
            ts_micros=6000000,
            ts_iso="2026-10-18T10:00:06Z",
            source="fluent",
            node="node-2",
            namespace="onprem",
            pod="dscc-console-ui-7cf6485746-fm29t",
            container="ui",
            level="INFO",
            issue_type="General",
            message="Normal pod health probe ping succeeded",
            file="ui.log",
            line_num=113,
        ),
    ]
    store.insert_events(events)

    # Insert k8s facts
    facts = [
        K8sFact(
            ts_micros=5000000,
            kind="Pod",
            namespace="onprem",
            name="dscc-console-ui-7cf6485746-fm29t",
            reason="OOMKilled",
            exit_code=137,
            restarts=4,
            detail="Memory limit exceeded in container ui",
        )
    ]
    store.insert_k8s_facts(facts)

    yield store
    store.close()


def test_patient_zero_detection(populated_store):
    """Test finding the earliest high-severity failure event."""
    pz = populated_store.get_patient_zero()
    assert pz is not None
    assert pz["ts_iso"] == "2026-10-18T10:00:01Z"
    assert pz["namespace"] == "auth"
    assert pz["pod"] == "vault-0"
    assert pz["level"] == "FATAL"
    assert "connection refused" in pz["message"]

    # Scoped to namespace onprem
    pz_onprem = populated_store.get_patient_zero(namespace="onprem")
    assert pz_onprem is not None
    assert pz_onprem["namespace"] == "onprem"
    assert pz_onprem["pod"] == "dscc-console-ui-7cf6485746-fm29t"
    assert pz_onprem["issue_type"] == "OOM Killed"


def test_drain_mined_templates(populated_store):
    """Test clustering recurring log messages into Drain templates."""
    templates = populated_store.get_mined_templates(limit=5)
    assert len(templates) >= 2

    # Verify that the two vault connection refused lines clustered together
    vault_cluster = next((t for t in templates if "dial tcp" in t["template"] or "connection refused" in t["template"]), None)
    assert vault_cluster is not None
    assert vault_cluster["count"] >= 2
    assert "<*>" in vault_cluster["template"]  # Wildcard mask for IP/port


def test_scored_events_retrieval(populated_store):
    """Test BM25/lexical scored event retrieval with relevance weighting."""
    # Query matching OOM specifically
    results_oom = populated_store.get_scored_events("dscc-console-ui out of memory exit 137", limit=5)
    assert len(results_oom) >= 1
    top_result = results_oom[0]
    assert top_result["pod"] == "dscc-console-ui-7cf6485746-fm29t"
    assert top_result["issue_type"] == "OOM Killed"
    assert top_result["relevance_score"] > 8.0

    # Query matching vault connection refused
    results_vault = populated_store.get_scored_events("vault connection refused", limit=5)
    assert len(results_vault) >= 1
    assert results_vault[0]["namespace"] == "auth"
    assert results_vault[0]["pod"] == "vault-0"


def test_get_ai_context_comprehensive(populated_store):
    """Test that get_ai_context compiles all high-density sections."""
    context = populated_store.get_ai_context(
        user_query="Why did dscc-console-ui crash?",
        namespace=None,
        limit=5,
    )

    # Verify all expected sections are present
    assert "### Active Bundle Overview" in context
    assert "### Patient Zero Candidate (Earliest Failure in Scope):" in context
    assert "vault-0" in context  # Patient Zero
    assert "### Dominant Failure Patterns (Drain Template Mining):" in context
    assert "### Kubernetes Pod Anomalies & Restarts:" in context
    assert "dscc-console-ui-7cf6485746-fm29t" in context
    assert "exit_code=137" in context
    assert "### Relevant Live Log Events" in context
