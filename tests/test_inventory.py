"""Unit and integration tests for bundle inventory, selective unit filters, and JobQueue."""

import io
import tarfile
import tempfile
from pathlib import Path

import pytest

from analyzer.ingest.inventory import (
    Inventory,
    Part,
    Group,
    Unit,
    build_inventory,
    make_unit_path_filter,
)
from analyzer.server.jobs import JobManager, JobQueue
from analyzer.store.duck import SessionStore


def test_build_inventory_cluster_and_host(tmp_path):
    # Build a simulated multi-part bundle
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tf:
        # 1. Cluster log entry
        c_data = (
            b'{"date":"2026-08-09T12:00:00Z","log":"[INFO] User login\\n",'
            b'"kubernetes":{"namespace_name":"ccs-system","pod_name":"worker-0","container_name":"worker"}}\n'
        )
        ti1 = tarfile.TarInfo(name="cluster-logs/ccs-system/worker-0/worker/2026/08/09/log.log")
        ti1.size = len(c_data)
        tf.addfile(ti1, io.BytesIO(c_data))

        # 2. Host snapshot node syslog
        syslog_data = b"Aug  9 12:00:00 node-1 kernel: [0.000000] Linux version 5.15.0\n"
        ti2 = tarfile.TarInfo(name="host-snapshot/hostlogssb-node-1/var/log/syslog")
        ti2.size = len(syslog_data)
        tf.addfile(ti2, io.BytesIO(syslog_data))

        # 3. Host snapshot pod log
        pod_log_data = b"2026-08-09T12:01:00.123Z stderr F vault starting up\n"
        ti3 = tarfile.TarInfo(name="host-snapshot/hostlogssb-node-1/kubernetes/base/namespaces/vault/pods/logs/vault-0.log")
        ti3.size = len(pod_log_data)
        tf.addfile(ti3, io.BytesIO(pod_log_data))

    bundle_path = tmp_path / "test_bundle.tar"
    bundle_path.write_bytes(tar_buf.getvalue())

    # Build inventory
    inv = build_inventory(bundle_path)
    assert len(inv.parts) >= 2

    part_ids = {p.id for p in inv.parts}
    assert "cluster" in part_ids
    assert any("node-1" in pid for pid in part_ids)

    # Check units
    all_units = inv.get_all_units()
    unit_ids = {u.id for u in all_units}
    assert "cluster:services:ccs-system" in unit_ids
    assert any("syslog" in uid for uid in unit_ids)
    assert any("vault" in uid for uid in unit_ids)

    # Find ccs unit
    ccs_unit = inv.find_unit("cluster:services:ccs-system")
    assert ccs_unit is not None
    assert ccs_unit.namespace == "ccs-system"
    assert "worker" in ccs_unit.containers
    assert ccs_unit.file_count == 1
    assert ccs_unit.date_min == "2026-08-09"


def test_make_unit_path_filter(tmp_path):
    inv = Inventory(source="test")
    part = Part(id="cluster", name="Cluster Logs", kind="cluster")
    group = Group(id="services", name="Services")
    unit = Unit(
        id="cluster:services:vault",
        name="vault",
        part_id="cluster",
        group="services",
        source_type="fluent",
        namespace="vault",
        path_prefixes=["cluster-logs/vault/"],
    )
    group.units.append(unit)
    part.groups.append(group)
    inv.parts.append(part)

    path_filter = make_unit_path_filter(["cluster:services:vault"], inv)
    assert path_filter("cluster-logs/vault/vault-0/vault/log.log", False) is True
    assert path_filter("cluster-logs/other/pod/container/log.log", False) is False


def test_job_queue_priority_order_and_reorder(tmp_path):
    inv = Inventory(source="test")
    part = Part(id="p1", name="Part 1", kind="cluster")
    group = Group(id="g1", name="Group 1")
    u1 = Unit(id="unit-1", name="Unit 1", part_id="p1", group="g1", source_type="log")
    u2 = Unit(id="unit-2", name="Unit 2", part_id="p1", group="g1", source_type="log")
    u3 = Unit(id="unit-3", name="Unit 3", part_id="p1", group="g1", source_type="log")
    group.units.extend([u1, u2, u3])
    part.groups.append(group)
    inv.parts.append(part)

    bundle_path = tmp_path / "bundle.tar"
    db_path = tmp_path / "session.duckdb"

    # Create dummy queue without background execution
    q = JobQueue(session_id="test_sess", bundle_path=bundle_path, db_path=db_path, inventory=inv)

    # Enqueue in priority order: unit-2, unit-1, unit-3
    q._lock.acquire()
    for uid in ["unit-2", "unit-1", "unit-3"]:
        unit = inv.find_unit(uid)
        q._items.append({
            "unit_id": uid,
            "name": unit.name,
            "part_id": unit.part_id,
            "group": unit.group,
            "status": "pending",
            "files": 0,
            "events": 0,
            "error": None,
            "started_at": None,
            "finished_at": None,
        })
    q._lock.release()

    status = q.status()
    assert [i["unit_id"] for i in status["items"]] == ["unit-2", "unit-1", "unit-3"]

    # Reorder pending units: move unit-3 to first
    q.reorder(["unit-3", "unit-2", "unit-1"])
    status2 = q.status()
    assert [i["unit_id"] for i in status2["items"]] == ["unit-3", "unit-2", "unit-1"]

    # Cancel queue
    q.cancel()
    status3 = q.status()
    assert all(i["status"] == "cancelled" for i in status3["items"])
