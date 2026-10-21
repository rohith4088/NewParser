"""Tests for the ingestion layer: archive walker, classifier, manifest, and scanner."""

import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from analyzer.ingest import (
    FAILED,
    FOUND,
    NOT_FOUND,
    PARTIAL,
    ArchiveSafetyError,
    Classification,
    Issue,
    Limits,
    Manifest,
    SourceCoverage,
    SourceType,
    classify,
    classify_path,
    path_hints,
    scan,
    walk,
)
from analyzer.ingest.archive import sanitize_path, strip_archive_suffix


# ==============================================================================
# 1. Archive Safety & Path Sanitization Tests
# ==============================================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("var/log/syslog", "var/log/syslog"),
        ("./var/./log//syslog", "var/log/syslog"),
        ("var\\log\\syslog", "var/log/syslog"),
        ("/etc/passwd", None),
        ("C:\\Windows\\System32", None),
        ("../../../etc/shadow", None),
        ("var/../../etc/shadow", None),
        ("safe/path/../escaped", None),
        ("bad\x00path", None),
        ("", None),
        (".", None),
    ],
)
def test_sanitize_path(raw, expected):
    assert sanitize_path(raw) == expected


def test_strip_archive_suffix():
    assert strip_archive_suffix("bundle/node-1.tar.gz") == "bundle/node-1"
    assert strip_archive_suffix("cluster/logs.tgz") == "cluster/logs"
    assert strip_archive_suffix("nested/archive.tar") == "nested/archive"
    assert strip_archive_suffix("backup.zip") == "backup"
    assert strip_archive_suffix("plain/file.txt") == "plain/file.txt"


def test_tar_path_traversal_rejected(tmp_path):
    tar_path = tmp_path / "traversal.tar"
    with tarfile.open(tar_path, "w") as tar:
        # Add a malicious member with .. in path
        data = b"malicious content"
        ti = tarfile.TarInfo(name="../../escaped.txt")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))

        # Add a benign member
        ti2 = tarfile.TarInfo(name="safe.txt")
        ti2.size = len(data)
        tar.addfile(ti2, io.BytesIO(data))

    items = list(walk(tar_path))
    issues = [item for item in items if isinstance(item, Issue)]
    members = [item for item in items if not isinstance(item, Issue)]

    assert any(i.kind == "unsafe_path" for i in issues)
    assert len(members) == 1
    assert members[0].path == "safe.txt"


def test_zip_path_traversal_rejected(tmp_path):
    zip_path = tmp_path / "traversal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../../etc/passwd", "root:x:0:0")
        zf.writestr("valid/log.txt", "valid log content")

    items = list(walk(zip_path))
    issues = [item for item in items if isinstance(item, Issue)]
    members = [item for item in items if not isinstance(item, Issue)]

    assert any(i.kind == "unsafe_path" for i in issues)
    assert len(members) == 1
    assert members[0].path == "valid/log.txt"


def test_compression_ratio_guard(tmp_path):
    # Create a small compressed file that expands to large repetitive bytes
    # Test that Limits(max_ratio=10.0, ratio_min_bytes=100) catches it
    tar_path = tmp_path / "bomb.tar.gz"
    payload = b"A" * (50 * 1024)  # 50 KB
    with tarfile.open(tar_path, "w:gz") as tar:
        ti = tarfile.TarInfo(name="huge.txt")
        ti.size = len(payload)
        tar.addfile(ti, io.BytesIO(payload))

    limits = Limits(max_ratio=5.0, ratio_min_bytes=500)
    items = list(walk(tar_path, limits=limits))
    issues = [item for item in items if isinstance(item, Issue)]
    assert any(i.kind == "ratio_limit" for i in issues)


def test_max_member_limit(tmp_path):
    tar_path = tmp_path / "many_files.tar"
    with tarfile.open(tar_path, "w") as tar:
        for i in range(10):
            data = b"ok"
            ti = tarfile.TarInfo(name=f"file_{i}.txt")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

    limits = Limits(max_members=5)
    items = list(walk(tar_path, limits=limits))
    issues = [item for item in items if isinstance(item, Issue)]
    members = [item for item in items if not isinstance(item, Issue)]

    assert len(members) == 5
    assert any(i.kind == "member_limit" for i in issues)


# ==============================================================================
# 2. Recursive & Multi-Format Walking Tests
# ==============================================================================


def test_nested_tar_within_tar(tmp_path):
    # Inner tar.gz
    inner_tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=inner_tar_bytes, mode="w:gz") as inner:
        data = b"inner log line 1\ninner log line 2"
        ti = tarfile.TarInfo(name="app/server.log")
        ti.size = len(data)
        inner.addfile(ti, io.BytesIO(data))
    inner_tar_bytes.seek(0)

    # Outer tar
    outer_tar_path = tmp_path / "outer_bundle.tar"
    with tarfile.open(outer_tar_path, "w") as outer:
        # Add a direct file
        d1 = b"host message"
        ti1 = tarfile.TarInfo(name="host/syslog")
        ti1.size = len(d1)
        outer.addfile(ti1, io.BytesIO(d1))

        # Add the nested tar.gz
        inner_data = inner_tar_bytes.getvalue()
        ti2 = tarfile.TarInfo(name="containers/cluster-logs.tar.gz")
        ti2.size = len(inner_data)
        outer.addfile(ti2, io.BytesIO(inner_data))

    items = list(walk(outer_tar_path))
    members = [item for item in items if not isinstance(item, Issue)]
    paths = [m.path for m in members]

    assert "host/syslog" in paths
    # Note nested archive stripped suffix: containers/cluster-logs/app/server.log
    assert "containers/cluster-logs/app/server.log" in paths

    # Verify content readable
    inner_member = next(m for m in members if m.path == "containers/cluster-logs/app/server.log")
    content = inner_member.stream.read()
    assert b"inner log line 1" in content


def test_walk_extracted_directory(tmp_path):
    # Directory with plain files, gzip files, and an unexpanded archive
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "plain.log").write_text("hello plain text")
    
    # Write gzipped file
    import gzip
    with gzip.open(sub / "compressed.log.gz", "wb") as f:
        f.write(b"decompressed content")

    # Write a tar archive (should be marked is_archive=True in dir mode)
    with tarfile.open(sub / "nested.tar", "w") as tar:
        d = b"inside"
        ti = tarfile.TarInfo(name="inner.txt")
        ti.size = len(d)
        tar.addfile(ti, io.BytesIO(d))

    found = {}
    for item in walk(tmp_path):
        if not isinstance(item, Issue):
            found[item.path] = (item.stream.read(), item.is_archive)

    assert "sub/plain.log" in found
    assert "sub/compressed.log.gz" in found
    assert "sub/nested.tar" in found

    # Verify transparent decompression
    assert found["sub/compressed.log.gz"][0] == b"decompressed content"
    assert found["sub/plain.log"][0] == b"hello plain text"
    assert found["sub/nested.tar"][1] is True


# ==============================================================================
# 3. Classifier Tests
# ==============================================================================


def test_path_hints_extraction():
    path = "hostlogssb/hostlogssb-node-2/docker/kube.var.log.containers.coredns-6f89-abc_kube-system_coredns-1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef.log"
    hints = path_hints(path)
    assert hints["node"] == "hostlogssb-node-2"
    assert hints["namespace"] == "kube-system"
    assert hints["pod"] == "coredns-6f89-abc"
    assert hints["container"] == "coredns"


def test_classify_fluent_json():
    # JSON payload
    data = json.dumps({
        "date": "2026-08-10T12:00:00Z",
        "message": "database connected",
        "kubernetes": {"namespace_name": "onprem", "pod_name": "test-pod"}
    }).encode("utf-8")

    cl = classify("unknown/path/output.log", data)
    assert cl.source_type == SourceType.FLUENT
    assert cl.reason == "content"


def test_classify_syslog_by_path_and_content():
    # By path
    cl_path = classify("host-1/var/log/messages", b"random content here")
    assert cl_path.source_type == SourceType.SYSLOG

    # By BSD syslog content at unfamiliar path
    bsd_line = b"Oct 14 07:15:02 myhost systemd[1]: Started Service.\n"
    cl_bsd = classify("random/debug.out", bsd_line)
    assert cl_bsd.source_type == SourceType.SYSLOG

    # By ISO syslog content
    iso_line = b"2026-08-10T07:22:15.123456+00:00 node1 kubelet[123]: I0810 starting pod\n"
    cl_iso = classify("random/klog.txt", iso_line)
    assert cl_iso.source_type == SourceType.SYSLOG


def test_classify_journal_binary_magic():
    # Systemd journal magic LPKSHHRH
    magic = b"LPKSHHRH" + b"\x00" * 100
    cl = classify("unknown/file", magic)
    assert cl.source_type == SourceType.JOURNAL
    assert cl.reason == "magic"

    # File named .journal but containing plain text is not a journal
    cl_fake = classify("fake.journal", b"just regular text")
    assert cl_fake.source_type != SourceType.JOURNAL


def test_classify_k8s_state_and_events():
    k8s_yaml = b"apiVersion: v1\nkind: Pod\nmetadata:\n  name: test-pod\n"
    cl = classify("dump/resource.yaml", k8s_yaml)
    assert cl.source_type == SourceType.K8S_STATE

    cl_events = classify("kubernetes/events/events.yaml", k8s_yaml)
    assert cl_events.source_type == SourceType.K8S_EVENTS


def test_classify_empty_and_binary_fallback():
    cl_empty = classify("empty.txt", b"", size=0)
    assert cl_empty.source_type == SourceType.EMPTY

    cl_bin = classify("data.bin", b"\x00\x01\x02\x03\x04")
    assert cl_bin.source_type == SourceType.BINARY

    cl_text = classify("notes.txt", b"General diagnostics notes\nNothing special")
    assert cl_text.source_type == SourceType.TEXT


# ==============================================================================
# 4. Coverage Manifest & Scanner Tests
# ==============================================================================


def test_manifest_coverage_statuses():
    cov = SourceCoverage()
    assert cov.status == NOT_FOUND

    cov.files = 5
    cov.failed_files = 0
    assert cov.status == FOUND

    cov.failed_files = 2
    assert cov.status == PARTIAL

    cov.failed_files = 5
    assert cov.status == FAILED


def test_manifest_recording_and_serialization():
    m = Manifest(source="test-bundle.tar")
    cl_fluent = Classification(SourceType.FLUENT, "path", {"namespace": "ccs-system", "node": "node-1"})
    m.add_member("fluent/pod.log", cl_fluent, size=1024)

    assert m.total_files == 1
    assert m.total_bytes == 1024
    assert "node-1" in m.nodes
    assert "ccs-system" in m.namespaces
    assert m.status_of(SourceType.FLUENT) == FOUND
    assert m.status_of(SourceType.JOURNAL) == NOT_FOUND

    d = m.to_dict()
    assert d["source"] == "test-bundle.tar"
    assert d["complete"] is True
    assert d["sources"]["fluent"]["status"] == FOUND
    assert d["sources"]["journal"]["status"] == NOT_FOUND


def test_scan_full_synthetic_bundle(tmp_path):
    bundle_path = tmp_path / "bundle.tar"
    with tarfile.open(bundle_path, "w") as tar:
        # 1. Syslog
        syslog_data = b"Aug 10 12:00:00 node-1 kernel: disk ok\n"
        ti = tarfile.TarInfo(name="hostlogssb-node-1/var/log/messages")
        ti.size = len(syslog_data)
        tar.addfile(ti, io.BytesIO(syslog_data))

        # 2. K8s YAML
        k8s_data = b"apiVersion: v1\nkind: PodList\nitems: []\n"
        ti = tarfile.TarInfo(name="kubernetes/base/pods.yaml")
        ti.size = len(k8s_data)
        tar.addfile(ti, io.BytesIO(k8s_data))

        # 3. Usage
        usage_data = b"Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 100G 20G 80G 20% /\n"
        ti = tarfile.TarInfo(name="usage/disk_usage.txt")
        ti.size = len(usage_data)
        tar.addfile(ti, io.BytesIO(usage_data))

    seen_members = []
    manifest = scan(bundle_path, on_member=lambda m, cl: seen_members.append(m.path))

    assert manifest.complete is True
    assert manifest.total_files == 3
    assert len(seen_members) == 3
    assert "hostlogssb-node-1" in manifest.nodes
    assert manifest.status_of(SourceType.SYSLOG) == FOUND
    assert manifest.status_of(SourceType.K8S_STATE) == FOUND
    assert manifest.status_of(SourceType.USAGE) == FOUND
    assert manifest.status_of(SourceType.JOURNAL) == NOT_FOUND


def test_scan_error_isolation(tmp_path):
    # Tests that when on_member fails on one file, the scan continues for other files
    bundle_path = tmp_path / "flaky.tar"
    with tarfile.open(bundle_path, "w") as tar:
        for name in ["file1.log", "broken.log", "file2.log"]:
            d = b"log line"
            ti = tarfile.TarInfo(name=name)
            ti.size = len(d)
            tar.addfile(ti, io.BytesIO(d))

    def flaky_callback(member, cl):
        if "broken" in member.path:
            raise RuntimeError("simulated read fault")

    manifest = scan(bundle_path, on_member=flaky_callback)

    assert manifest.total_files == 3
    # broken.log recorded read failure
    assert len(manifest.issues) == 1
    assert manifest.issues[0].path == "broken.log"
    assert "simulated read fault" in manifest.issues[0].message


def test_peekable_line_iteration():
    from analyzer.ingest.archive import Peekable
    raw = io.BytesIO(b"line 1\nline 2\r\nline 3\n")
    p = Peekable(raw)
    assert p.peek(4) == b"line"
    lines = list(p)
    assert lines == [b"line 1\n", b"line 2\r\n", b"line 3\n"]

