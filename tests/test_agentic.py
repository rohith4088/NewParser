"""Unit tests for the Hierarchical Multi-Agent Triage (H-MAT) Framework."""

import pytest
from analyzer.agentic.agents import (
    ClusterDomainAgent,
    HostDomainAgent,
    HostSubAgent,
    IncidentCommanderAgent,
    NamespaceSubAgent,
)
from analyzer.agentic.context_builder import AgentContextBuilder
from analyzer.agentic.models import AgentRole, AgentStatus, AgentVerdict
from analyzer.agentic.orchestrator import AgenticTriageOrchestrator
from analyzer.parse.event import Event
from analyzer.parse.k8s_state import K8sFact
from analyzer.parse.timestamps import parse_iso_micros
from analyzer.store.duck import SessionStore


@pytest.fixture
def multi_namespace_store():
    """Create an in-memory SessionStore with a realistic cascading failure across namespaces."""
    store = SessionStore(":memory:")
    store.set_metadata("session_agentic_001", "support_bundle.tar", "2026-10-18T10:00:00Z", total_files=12)

    # Cascading scenario:
    # 1. 10:00:01 - storage driver failure on host (Patient Zero)
    # 2. 10:00:05 - auth/vault database error due to read-only filesystem
    # 3. 10:00:10 - kube-system/coredns SERVFAIL
    # 4. 10:00:15 - onprem/frontend 502 Bad Gateway
    t1 = parse_iso_micros("2026-10-18T10:00:01Z")
    t2 = parse_iso_micros("2026-10-18T10:00:05Z")
    t3 = parse_iso_micros("2026-10-18T10:00:06Z")
    t4 = parse_iso_micros("2026-10-18T10:00:10Z")
    t5 = parse_iso_micros("2026-10-18T10:00:15Z")

    events = [
        Event(
            ts_micros=t1,
            ts_iso="2026-10-18T10:00:01Z",
            source="host",
            node="node-1",
            namespace=None,
            pod=None,
            container=None,
            level="FATAL",
            issue_type="Storage I/O Error",
            message="kernel: [sdc] Buffer I/O error on dev sdc1, logical block 4096, async page read",
            file="kern.log",
            line_num=42,
            unit_id="kernel_storage",
        ),
        Event(
            ts_micros=t2,
            ts_iso="2026-10-18T10:00:05Z",
            source="cluster",
            node="node-1",
            namespace="auth",
            pod="vault-0",
            container="vault",
            level="FATAL",
            issue_type="Panic/Crash",
            message="panic: failed to flush write-ahead-log: Read-only file system",
            file="vault.log",
            line_num=101,
        ),
        Event(
            ts_micros=t3,
            ts_iso="2026-10-18T10:00:06Z",
            source="cluster",
            node="node-1",
            namespace="auth",
            pod="vault-0",
            container="vault",
            level="ERROR",
            issue_type="Connection Refused",
            message="dial tcp 10.0.0.1:8200: connect: connection refused",
            file="vault.log",
            line_num=102,
        ),
        Event(
            ts_micros=t4,
            ts_iso="2026-10-18T10:00:10Z",
            source="cluster",
            node="node-2",
            namespace="kube-system",
            pod="coredns-1",
            container="coredns",
            level="ERROR",
            issue_type="DNS Failure",
            message="plugin/errors: 2 auth.vault.svc.cluster.local. A: SERVFAIL",
            file="coredns.log",
            line_num=55,
        ),
        Event(
            ts_micros=t5,
            ts_iso="2026-10-18T10:00:15Z",
            source="cluster",
            node="node-2",
            namespace="onprem",
            pod="dscc-web-frontend",
            container="nginx",
            level="ERROR",
            issue_type="HTTP 502",
            message="connect() failed (111: Connection refused) while connecting to upstream",
            file="nginx.log",
            line_num=89,
        ),
    ]

    store.insert_events(events)

    facts = [
        K8sFact(
            ts_micros=t2 + 100_000,
            kind="Pod",
            namespace="auth",
            name="vault-0",
            reason="CrashLoopBackOff",
            exit_code=1,
            restarts=3,
            detail="Back-off restarting failed container vault in pod vault-0",
        ),
        K8sFact(
            ts_micros=t5 + 200_000,
            kind="Pod",
            namespace="onprem",
            name="dscc-web-frontend",
            reason="OOMKilled",
            exit_code=137,
            restarts=1,
            detail="Process exceeded memory limit",
        ),
    ]
    store.insert_k8s_facts(facts)

    return store


def test_context_builder(multi_namespace_store):
    cb = AgentContextBuilder()
    auth_ctx = cb.build_namespace_context(multi_namespace_store, "auth")

    assert auth_ctx["namespace"] == "auth"
    assert auth_ctx["fatal_count"] == 1
    assert auth_ctx["error_count"] == 1
    assert auth_ctx["first_fault_micros"] == parse_iso_micros("2026-10-18T10:00:05Z")
    assert "2026-10-18T10:00:05" in auth_ctx["first_fault_iso"]
    assert len(auth_ctx["k8s_facts"]) == 1
    assert auth_ctx["k8s_facts"][0]["reason"] == "CrashLoopBackOff"

    host_ctx = cb.build_host_context(multi_namespace_store, "kernel_storage")
    assert host_ctx["fatal_count"] == 1
    assert host_ctx["first_fault_micros"] == parse_iso_micros("2026-10-18T10:00:01Z")
    assert "2026-10-18T10:00:01" in host_ctx["first_fault_iso"]


def test_sub_agents_analysis(multi_namespace_store):
    cb = AgentContextBuilder()

    auth_agent = NamespaceSubAgent("auth", use_llm=False)
    auth_ctx = cb.build_namespace_context(multi_namespace_store, "auth")
    auth_verdict = auth_agent.analyze(auth_ctx)

    assert auth_verdict.status == AgentStatus.CRITICAL
    assert auth_verdict.role == AgentRole.SUB_NAMESPACE
    assert auth_verdict.earliest_fault_micros == parse_iso_micros("2026-10-18T10:00:05Z")
    assert "CrashLoopBackOff" in auth_verdict.primary_issue or "Read-only" in auth_verdict.primary_issue

    host_agent = HostSubAgent("kernel_storage", use_llm=False)
    host_ctx = cb.build_host_context(multi_namespace_store, "kernel_storage")
    host_verdict = host_agent.analyze(host_ctx)

    assert host_verdict.status == AgentStatus.CRITICAL
    assert host_verdict.role == AgentRole.SUB_HOST
    assert host_verdict.earliest_fault_micros == parse_iso_micros("2026-10-18T10:00:01Z")


def test_orchestrator_dynamic_discovery_and_patient_zero(multi_namespace_store):
    orchestrator = AgenticTriageOrchestrator(multi_namespace_store, use_llm=False)
    targets = orchestrator.discover_targets()

    # Verify discovered namespaces
    assert "auth" in targets["namespaces"]
    assert "kube-system" in targets["namespaces"]
    assert "onprem" in targets["namespaces"]
    assert "kernel_storage" in targets["host_units"]

    # Run the full agentic stream
    events = list(orchestrator.run_stream("session_agentic_001"))
    assert len(events) >= 5

    # Check that tree was discovered event
    tree_evt = events[0]
    assert tree_evt["type"] == "tree_discovered"
    assert tree_evt["total_agents"] >= 5

    # Check final completion event
    last_evt = events[-1]
    assert last_evt["type"] == "triage_completed"
    report = last_evt["report"]

    # Verify that Patient Zero is correctly identified as the earliest fault:
    # kernel_storage at 10:00:01Z (earlier than vault at 10:00:05Z)
    assert report["patient_zero_component"] == "kernel_storage"
    assert "2026-10-18T10:00:01" in report["patient_zero_iso"]
    assert report["overall_status"] == "critical"

    # Verify Causal Chain sequence
    chain = report["causal_chain"]
    assert len(chain) >= 3
    assert chain[0]["component"] == "kernel_storage"
    assert chain[0]["is_patient_zero"] is True
    assert chain[1]["component"] == "auth"

    # Verify remediation playbook
    playbook = report["remediation_playbook"]
    assert len(playbook) > 0
    assert any("kernel_storage" in cmd for cmd in playbook)


def test_agent_llm_json_enhancement(multi_namespace_store):
    cb = AgentContextBuilder()
    agent = NamespaceSubAgent("auth", use_llm=True)
    
    # Mock _query_llm to simulate Qwen 2.5:3B JSON output
    agent._query_llm = lambda sys, user: {
        "status": "CRITICAL",
        "primary_issue": "Local LLM identified Vault storage backend crash",
        "causal_hypothesis": "Read-only file system caused disk write lock in Vault WAL engine.",
        "confidence": 0.99,
    }
    
    ctx = cb.build_namespace_context(multi_namespace_store, "auth")
    verdict = agent.analyze(ctx)

    assert verdict.status == AgentStatus.CRITICAL
    assert verdict.primary_issue == "Local LLM identified Vault storage backend crash"
    assert "Read-only file system" in verdict.causal_hypothesis

