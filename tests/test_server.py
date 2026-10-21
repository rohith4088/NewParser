"""Integration tests for the HTTP server, REST API, and static asset serving."""

import io
import json
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path

from analyzer.config import Config
from analyzer.server import create_server


def _start_test_server(tmp_path):
    config = Config(
        data_dir=tmp_path / "data",
        bundles_dir=tmp_path / "bundles",
        rules_dir=Path(__file__).resolve().parents[1] / "rules",
        topology_path=Path(__file__).resolve().parents[1] / "topology" / "services.yaml",
        host="127.0.0.1",
        port=0,  # OS allocates random available port
    )
    server = create_server(config)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    return server, base_url, config


def test_server_static_and_health(tmp_path):
    server, base_url, _ = _start_test_server(tmp_path)
    try:
        # 1. Health check
        with urllib.request.urlopen(f"{base_url}/api/health") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "ok"

        # 2. Static index.html
        with urllib.request.urlopen(f"{base_url}/") as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "OnPrem Analyzer" in body
            assert "Air-Gapped" in body

        # 3. Static style.css
        with urllib.request.urlopen(f"{base_url}/style.css") as resp:
            assert resp.status == 200
            css = resp.read().decode("utf-8")
            assert "--bg-primary" in css

        # 4. Static app.js
        with urllib.request.urlopen(f"{base_url}/app.js") as resp:
            assert resp.status == 200
            js = resp.read().decode("utf-8")
            assert "checkSessions" in js
    finally:
        server.shutdown()
        server.server_close()


def test_server_upload_and_query_flow(tmp_path):
    server, base_url, config = _start_test_server(tmp_path)
    try:
        # Create a test tar bundle in memory
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tf:
            fluent_data = (
                b'{"date":"2026-08-09T12:00:00Z","log":"[ERROR] Vault connection refused\\n",'
                b'"kubernetes":{"namespace_name":"vault","pod_name":"vault-0","container_name":"vault"}}\n'
                b'{"date":"2026-08-09T12:05:00Z","log":"[INFO] Routine check\\n",'
                b'"kubernetes":{"namespace_name":"vault","pod_name":"vault-0","container_name":"vault"}}\n'
            )
            ti = tarfile.TarInfo(name="fluent/node-1/vault/vault-0.log")
            ti.size = len(fluent_data)
            tf.addfile(ti, io.BytesIO(fluent_data))

            k8s_data = b"""
apiVersion: v1
kind: Pod
metadata:
  name: vault-0
  namespace: vault
status:
  containerStatuses:
  - name: vault
    restartCount: 5
    lastState:
      terminated:
        reason: OOMKilled
        exitCode: 137
"""
            ti2 = tarfile.TarInfo(name="kubernetes/base/namespaces/vault/pods/vault-0.yaml")
            ti2.size = len(k8s_data)
            tf.addfile(ti2, io.BytesIO(k8s_data))

        tar_bytes = tar_buf.getvalue()

        # 1. Upload bundle via POST /api/upload
        req = urllib.request.Request(
            f"{base_url}/api/upload",
            data=tar_bytes,
            headers={"Content-Type": "application/x-tar", "X-File-Name": "my_bundle.tar"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            assert res["status"] == "ok"
            assert res["session_id"] == "my_bundle"

        # 2. List sessions
        with urllib.request.urlopen(f"{base_url}/api/sessions") as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["active_session"] == "my_bundle"
            assert len(data["sessions"]) == 1
            assert data["sessions"][0]["id"] == "my_bundle"

        # 3. Query metadata
        with urllib.request.urlopen(f"{base_url}/api/metadata") as resp:
            meta = json.loads(resp.read().decode("utf-8"))
            assert meta["total_events"] == 2

        # 4. Query stats
        with urllib.request.urlopen(f"{base_url}/api/stats") as resp:
            stats = json.loads(resp.read().decode("utf-8"))
            assert stats["levels"]["ERROR"] == 1
            assert stats["levels"]["INFO"] == 1
            assert stats["namespaces"]["vault"] == 2

        # 5. Query events
        with urllib.request.urlopen(f"{base_url}/api/events?level=ERROR") as resp:
            evs = json.loads(resp.read().decode("utf-8"))
            assert len(evs["events"]) == 1
            assert evs["events"][0]["namespace"] == "vault"
            assert evs["events"][0]["level"] == "ERROR"

        # 6. Query findings
        with urllib.request.urlopen(f"{base_url}/api/findings") as resp:
            findings_data = json.loads(resp.read().decode("utf-8"))
            findings = findings_data["findings"]
            assert len(findings) >= 1
            # OOM or Vault connection refused finding should trigger
            rule_ids = {f["rule_id"] for f in findings}
            assert ("K8S_OOM_KILLED_POD" in rule_ids or "VAULT_CONNECTION_REFUSED" in rule_ids)

        # 7. Query timeline
        with urllib.request.urlopen(f"{base_url}/api/timeline?buckets=10") as resp:
            tl = json.loads(resp.read().decode("utf-8"))
            assert "buckets" in tl

        # 8. Query k8s facts
        with urllib.request.urlopen(f"{base_url}/api/k8s_facts") as resp:
            k8s = json.loads(resp.read().decode("utf-8"))
            assert len(k8s["k8s_facts"]) >= 1
            assert k8s["k8s_facts"][0]["reason"] == "OOMKilled"
    finally:
        server.shutdown()
        server.server_close()


def test_server_selective_namespace_ingest_flow(tmp_path):
    server, base_url, config = _start_test_server(tmp_path)
    try:
        # 1. Query namespaces catalog before any upload
        with urllib.request.urlopen(f"{base_url}/api/bundle/namespaces") as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["ingested_count"] == 0
            assert "Application Microservices & APIs" in data["categories"]

        # 2. Query topology related
        with urllib.request.urlopen(f"{base_url}/api/topology/related?namespace=unified-api") as resp:
            rel_data = json.loads(resp.read().decode("utf-8"))
            assert rel_data["namespace"] == "unified-api"
            related_ns = {r["namespace"] for r in rel_data["related"]}
            assert "ccs-system" in related_ns
            assert "postgresql-ccs" in related_ns

        # 3. Create a multi-namespace tar bundle in bundles folder
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tf:
            f_ccs = (
                b'{"date":"2026-08-09T12:00:00Z","log":"[INFO] CCS login worker start\\n",'
                b'"kubernetes":{"namespace_name":"ccs-system","pod_name":"worker-0","container_name":"worker"}}\n'
            )
            ti = tarfile.TarInfo(name="fluent/node-1/ccs-system/worker-0.log")
            ti.size = len(f_ccs)
            tf.addfile(ti, io.BytesIO(f_ccs))

            f_pg = (
                b'{"date":"2026-08-09T12:01:00Z","log":"[ERROR] Postgres lock timeout\\n",'
                b'"kubernetes":{"namespace_name":"postgresql-ccs","pod_name":"pg-0","container_name":"pg"}}\n'
            )
            ti2 = tarfile.TarInfo(name="fluent/node-1/postgresql-ccs/pg-0.log")
            ti2.size = len(f_pg)
            tf.addfile(ti2, io.BytesIO(f_pg))

        bundle_path = config.bundles_dir / "test_multi.tar"
        bundle_path.write_bytes(tar_buf.getvalue())

        # Analyze with include_namespaces = ["ccs-system"]
        req = urllib.request.Request(
            f"{base_url}/api/bundles/analyze",
            data=json.dumps({"bundle_name": "test_multi.tar", "namespaces": ["ccs-system"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            assert res["status"] == "ok"

        # Check stats: only ccs-system is present (1 event), postgresql-ccs was skipped
        with urllib.request.urlopen(f"{base_url}/api/stats") as resp:
            stats = json.loads(resp.read().decode("utf-8"))
            assert "ccs-system" in stats["namespaces"]
            assert "postgresql-ccs" not in stats["namespaces"]

        # Now incrementally ingest postgresql-ccs on-demand!
        req_ingest = urllib.request.Request(
            f"{base_url}/api/sessions/ingest_namespace",
            data=json.dumps({"session_id": "test_multi", "namespaces": ["postgresql-ccs"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_ingest) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            assert res["status"] == "ok"
            assert "postgresql-ccs" in res["ingested_namespaces"]

        # Check stats again: both are now present!
        with urllib.request.urlopen(f"{base_url}/api/stats") as resp:
            stats = json.loads(resp.read().decode("utf-8"))
            assert "ccs-system" in stats["namespaces"]
            assert "postgresql-ccs" in stats["namespaces"]
            assert stats["namespaces"]["ccs-system"] == 1
            assert stats["namespaces"]["postgresql-ccs"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_server_inventory_and_priority_queue_flow(tmp_path):
    server, base_url, config = _start_test_server(tmp_path)
    try:
        # Create a bundle with two service namespaces
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tf:
            f_vault = (
                b'{"date":"2026-08-09T12:00:00Z","log":"[INFO] Vault initialized\\n",'
                b'"kubernetes":{"namespace_name":"vault","pod_name":"vault-0","container_name":"vault"}}\n'
            )
            ti = tarfile.TarInfo(name="cluster-logs/vault/vault-0/vault/2026/08/09/log.log")
            ti.size = len(f_vault)
            tf.addfile(ti, io.BytesIO(f_vault))

            f_ccs = (
                b'{"date":"2026-08-09T12:05:00Z","log":"[INFO] CCS login success\\n",'
                b'"kubernetes":{"namespace_name":"ccs-system","pod_name":"worker-0","container_name":"worker"}}\n'
            )
            ti2 = tarfile.TarInfo(name="cluster-logs/ccs-system/worker-0/worker/2026/08/09/log.log")
            ti2.size = len(f_ccs)
            tf.addfile(ti2, io.BytesIO(f_ccs))

        bundle_path = config.bundles_dir / "queue_test.tar"
        bundle_path.write_bytes(tar_buf.getvalue())

        # 1. Initialize session with mode: "inventory" (fast walk, no parsing)
        req = urllib.request.Request(
            f"{base_url}/api/bundles/analyze",
            data=json.dumps({"bundle_name": "queue_test.tar", "mode": "inventory"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            assert res["status"] == "ok"
            assert res["session_id"] == "queue_test"
            assert "inventory" in res
            assert len(res["inventory"]["parts"]) >= 1

        # 2. Query GET /api/inventory
        with urllib.request.urlopen(f"{base_url}/api/inventory?session_id=queue_test") as resp:
            assert resp.status == 200
            inv_data = json.loads(resp.read().decode("utf-8"))
            assert len(inv_data["parts"]) >= 1
            units = []
            for p in inv_data["parts"]:
                for g in p["groups"]:
                    units.extend(g["units"])
            u_ids = {u["id"] for u in units}
            assert "cluster:services:vault" in u_ids
            assert "cluster:services:ccs-system" in u_ids

        # 3. Before enqueueing, verify stats has 0 events
        with urllib.request.urlopen(f"{base_url}/api/stats") as resp:
            stats = json.loads(resp.read().decode("utf-8"))
            assert len(stats["namespaces"]) == 0

        # 4. Enqueue cluster:services:vault first
        req_job = urllib.request.Request(
            f"{base_url}/api/jobs",
            data=json.dumps({
                "session_id": "queue_test",
                "units": ["cluster:services:vault"],
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_job) as resp:
            assert resp.status == 200
            job_res = json.loads(resp.read().decode("utf-8"))
            assert job_res["status"] == "ok"
            assert job_res["queued_count"] == 1

        # 5. Wait for worker to finish processing the unit
        import time
        for _ in range(30):
            time.sleep(0.1)
            with urllib.request.urlopen(f"{base_url}/api/jobs?session_id=queue_test") as resp:
                q_status = json.loads(resp.read().decode("utf-8"))
                if not q_status["is_running"] and q_status["completed_count"] >= 1:
                    assert "overall_pct" in q_status
                    assert q_status["overall_pct"] == 100
                    assert q_status["failed_count"] == 0
                    assert "current_unit_files_done" in q_status
                    break

        # 6. Check stats: vault should be parsed (1 event), ccs-system is still not parsed
        with urllib.request.urlopen(f"{base_url}/api/stats") as resp:
            stats = json.loads(resp.read().decode("utf-8"))
            assert "vault" in stats["namespaces"]
            assert stats["namespaces"]["vault"] == 1
            assert "ccs-system" not in stats["namespaces"]

        # 7. Now enqueue ccs-system
        req_job2 = urllib.request.Request(
            f"{base_url}/api/jobs",
            data=json.dumps({
                "session_id": "queue_test",
                "units": ["cluster:services:ccs-system"],
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_job2) as resp:
            assert resp.status == 200

        for _ in range(30):
            time.sleep(0.1)
            with urllib.request.urlopen(f"{base_url}/api/jobs?session_id=queue_test") as resp:
                q_status = json.loads(resp.read().decode("utf-8"))
                if not q_status["is_running"] and q_status["completed_count"] >= 2:
                    assert q_status["overall_pct"] == 100
                    assert q_status["completed_count"] == 2
                    break

        # 8. Check stats: both are now present!
        with urllib.request.urlopen(f"{base_url}/api/stats") as resp:
            stats = json.loads(resp.read().decode("utf-8"))
            assert "vault" in stats["namespaces"]
            assert "ccs-system" in stats["namespaces"]
            assert stats["namespaces"]["vault"] == 1
            assert stats["namespaces"]["ccs-system"] == 1

    finally:
        server.shutdown()
        server.server_close()


def test_server_origin_differentiation_and_safety(tmp_path):
    server, base_url, config = _start_test_server(tmp_path)
    try:
        from analyzer.parse.event import Event
        from analyzer.store.duck import SessionStore

        db_path = config.sessions_dir / "origin_test.duckdb"
        store = SessionStore(db_path)
        
        # Insert a cluster event
        ev_cluster = Event(
            ts_micros=1785470700000000,
            ts_iso="2026-08-01T00:00:00Z",
            source="fluent",
            node="node-1",
            namespace="ccs-system",
            pod="frontend",
            container="frontend",
            level="INFO",
            issue_type="Info",
            message="Cluster frontend started",
            file="cluster.log",
            line_num=1,
        )
        # Insert a host event
        ev_host = Event(
            ts_micros=1785470701000000,
            ts_iso="2026-08-01T00:00:01Z",
            source="journal",
            node="node-1",
            namespace="systemd",
            pod="docker.service",
            container="docker",
            level="WARN",
            issue_type="Warning",
            message="Host docker restart",
            file="journal.log",
            line_num=2,
        )
        store.insert_events([ev_cluster], unit_id="cluster:services:ccs-system")
        store.insert_events([ev_host], unit_id="host-node-1:systemd:docker")
        store.close()

        # Switch to this session
        req_switch = urllib.request.Request(
            f"{base_url}/api/sessions/switch",
            data=json.dumps({"session_id": "origin_test"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_switch) as resp:
            assert resp.status == 200

        # Check /api/stats includes origins
        with urllib.request.urlopen(f"{base_url}/api/stats") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "origins" in data
            assert data["origins"]["cluster"] == 1
            assert data["origins"]["host"] == 1

        # Check /api/events?origin=cluster returns only cluster
        with urllib.request.urlopen(f"{base_url}/api/events?origin=cluster") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert len(data["events"]) == 1
            assert data["events"][0]["origin"] == "cluster"
            assert data["events"][0]["namespace"] == "ccs-system"

        # Check /api/events?origin=host returns only host
        with urllib.request.urlopen(f"{base_url}/api/events?origin=host") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert len(data["events"]) == 1
            assert data["events"][0]["origin"] == "host"
            assert data["events"][0]["namespace"] == "systemd"

        # Check /api/bundle/namespaces handles safely
        with urllib.request.urlopen(f"{base_url}/api/bundle/namespaces") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "categories" in data

    finally:
        server.shutdown()
        server.server_close()


def test_concurrent_timeline_and_api_requests(tmp_path):
    server, base_url, config = _start_test_server(tmp_path)
    try:
        from analyzer.parse.event import Event
        from analyzer.store.duck import SessionStore

        sess_id = "concurrent_test"
        db_path = config.sessions_dir / f"{sess_id}.duckdb"
        store = SessionStore(db_path, read_only=False)
        events = [
            Event(
                ts_micros=1785470700000000 + i * 1_000_000,
                ts_iso="2026-08-01T00:00:00Z",
                source="fluent",
                node="node-1",
                namespace="default",
                pod=f"pod-{i}",
                container="app",
                level="ERROR" if i % 2 == 0 else "INFO",
                issue_type="Error" if i % 2 == 0 else "",
                message=f"Event {i}",
                file="app.log",
                line_num=i,
            )
            for i in range(50)
        ]
        store.insert_events(events, unit_id="cluster:services:default")
        store.set_metadata(sess_id, f"{sess_id}.tar", "2026-08-01T00:00:00Z", total_files=1)
        store.close()

        # Switch to session
        req_switch = urllib.request.Request(
            f"{base_url}/api/sessions/switch",
            data=json.dumps({"session_id": sess_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_switch) as resp:
            assert resp.status == 200

        import concurrent.futures

        endpoints = [
            f"{base_url}/api/timeline?buckets=10",
            f"{base_url}/api/stats",
            f"{base_url}/api/events?limit=20",
            f"{base_url}/api/metadata",
            f"{base_url}/api/k8s_facts",
            f"{base_url}/api/anomalies",
            f"{base_url}/api/findings",
        ]

        errors = []

        def worker(url):
            try:
                with urllib.request.urlopen(url) as resp:
                    if resp.status != 200:
                        errors.append((url, resp.status))
            except Exception as exc:
                errors.append((url, str(exc)))

        # Fire requests concurrently across multiple rounds
        for _ in range(5):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                futures = [ex.submit(worker, u) for u in endpoints * 3]
                concurrent.futures.wait(futures)

        assert not errors, f"Concurrent request errors encountered: {errors}"
    finally:
        server.shutdown()
        server.server_close()


def test_server_agentic_triage_flow(tmp_path):
    server, base_url, config = _start_test_server(tmp_path)
    try:
        # 1. Check tree without session -> 404
        try:
            with urllib.request.urlopen(f"{base_url}/api/agentic/tree") as resp:
                pass
        except urllib.error.HTTPError as exc:
            assert exc.code == 404

        # 2. Upload test bundle to establish active session
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tf:
            fluent_data = (
                b'{"date":"2026-08-09T12:00:00Z","log":"[FATAL] panic: runtime error on disk\\n",'
                b'"kubernetes":{"namespace_name":"storage","pod_name":"storage-0","container_name":"storage"}}\n'
                b'{"date":"2026-08-09T12:05:00Z","log":"[ERROR] connection refused to upstream\\n",'
                b'"kubernetes":{"namespace_name":"onprem","pod_name":"app-1","container_name":"app"}}\n'
            )
            ti = tarfile.TarInfo(name="fluent/node-1/storage/storage-0.log")
            ti.size = len(fluent_data)
            tf.addfile(ti, io.BytesIO(fluent_data))
        tar_bytes = tar_buf.getvalue()

        req_upload = urllib.request.Request(
            f"{base_url}/api/upload",
            data=tar_bytes,
            headers={"Content-Type": "application/x-tar", "X-File-Name": "agentic_test.tar"},
            method="POST",
        )
        with urllib.request.urlopen(req_upload) as resp:
            assert resp.status == 200

        # 3. Discover Agent Tree via GET /api/agentic/tree
        with urllib.request.urlopen(f"{base_url}/api/agentic/tree") as resp:
            assert resp.status == 200
            tree_data = json.loads(resp.read().decode("utf-8"))
            assert "storage" in tree_data["namespaces"]
            assert "onprem" in tree_data["namespaces"]
            assert tree_data["total_agents"] >= 4

        # 4. Trigger Multi-Agent Triage via POST /api/agentic/triage
        triage_req = urllib.request.Request(
            f"{base_url}/api/agentic/triage",
            data=json.dumps({"model": "qwen2.5:3b", "use_llm": False}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(triage_req) as resp:
            assert resp.status == 200
            sse_content = ""
            for raw_line in resp:
                line = raw_line.decode("utf-8")
                sse_content += line
                if "triage_completed" in line:
                    break
            assert "tree_discovered" in sse_content
            assert "triage_completed" in sse_content
            assert "patient_zero_component" in sse_content

        # 5. Verify cached report via GET /api/agentic/report
        with urllib.request.urlopen(f"{base_url}/api/agentic/report") as resp:
            assert resp.status == 200
            rep_data = json.loads(resp.read().decode("utf-8"))
            assert rep_data["report"] is not None
            assert rep_data["report"]["patient_zero_component"] == "storage"
            assert rep_data["report"]["overall_status"] == "critical"
    finally:
        server.shutdown()
        server.server_close()




