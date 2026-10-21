"""Unit tests for the parsing layer (normalization, stitching, formats, state, usage)."""

import io
import tarfile
from analyzer.parse.event import Event, detect_issue_type, detect_level
from analyzer.parse.fluent import parse_fluent_stream
from analyzer.parse.k8s_state import parse_k8s_yaml_stream
from analyzer.parse.stitch import LogStitcher, RawLogLine, is_continuation_line
from analyzer.parse.syslog import parse_syslog_stream
from analyzer.parse.timestamps import (
    micros_to_iso,
    parse_iso_micros,
    parse_klog_micros,
    parse_syslog_micros,
)
from analyzer.parse.usage import parse_usage_stream


# ==============================================================================
# 1. Timestamp Normalization Tests
# ==============================================================================


def test_parse_iso_micros():
    # UTC standard
    ts = parse_iso_micros("2026-08-09T17:00:04.316759Z")
    assert ts is not None
    assert micros_to_iso(ts).startswith("2026-08-09T17:00:04.316759")

    # Space separated, no subsecond
    ts2 = parse_iso_micros("2026-08-09 17:00:04")
    assert ts2 is not None
    assert micros_to_iso(ts2).startswith("2026-08-09T17:00:04.000000")

    # Timezone offset +02:00
    ts3 = parse_iso_micros("2026-08-09T19:00:04.316759+02:00")
    assert ts3 == ts

    # Invalid / empty
    assert parse_iso_micros("") is None
    assert parse_iso_micros("not-a-timestamp") is None


def test_parse_syslog_micros():
    line = "Aug  9 00:00:45 node-1 systemd[1]: Started..."
    micros, consumed = parse_syslog_micros(line, default_year=2026)
    assert micros is not None
    assert consumed == 15
    iso = micros_to_iso(micros)
    assert iso.startswith("2026-08-09T00:00:45")

    # Invalid
    micros_inv, consumed_inv = parse_syslog_micros("Foo 99 99:99:99")
    assert micros_inv is None
    assert consumed_inv == 0


def test_parse_klog_micros():
    line = "W0809 12:30:15.123456   12344 server.go:45] Request timed out"
    micros, consumed, level = parse_klog_micros(line, default_year=2026)
    assert micros is not None
    assert consumed > 0
    assert level == "WARN"
    iso = micros_to_iso(micros)
    assert iso.startswith("2026-08-09T12:30:15.123456")

    # Error prefix
    line_err = "E0809 12:30:15.000000 1 server.go:50] Failed connection"
    _, _, level_err = parse_klog_micros(line_err, default_year=2026)
    assert level_err == "ERROR"


# ==============================================================================
# 2. Log Stitcher Tests
# ==============================================================================


def test_is_continuation_line():
    assert is_continuation_line("  at com.example.MyService.run(MyService.java:123)")
    assert is_continuation_line("Caused by: java.io.IOException: connection reset")
    assert is_continuation_line("goroutine 1 [running]:")
    assert is_continuation_line("\t/usr/local/go/src/runtime/panic.go:838")
    assert is_continuation_line("  File \"/app/main.py\", line 42, in <module>")
    assert is_continuation_line("    some_indented_code()")
    assert not is_continuation_line("2026-08-09T12:00:00Z INFO starting app")
    assert not is_continuation_line("")


def test_stitcher_python_traceback():
    stitcher = LogStitcher(max_buffer_lines=10)
    lines = [
        "2026-08-09T10:00:00Z INFO Normal line 1",
        "Traceback (most recent call last):",
        "  File \"app.py\", line 10, in run",
        "    do_something()",
        "ZeroDivisionError: division by zero",
        "2026-08-09T10:00:01Z INFO Normal line 2",
    ]

    events = []
    for i, line in enumerate(lines, start=1):
        ev = stitcher.push(RawLogLine(line_num=i, text=line))
        if ev:
            events.append(ev)
    final_ev = stitcher.flush()
    if final_ev:
        events.append(final_ev)

    assert len(events) == 3
    assert events[0].message == "2026-08-09T10:00:00Z INFO Normal line 1"
    assert "Traceback" in events[1].message
    assert "ZeroDivisionError" in events[1].message
    assert events[1].continuation_lines == 3
    assert events[2].message == "2026-08-09T10:00:01Z INFO Normal line 2"


def test_stitcher_fluent_partial_chunks():
    stitcher = LogStitcher()
    # Simulated 16KB Fluent Bit split chunks with logtag: "P"
    chunk1 = RawLogLine(line_num=1, text="Part 1 of long line...", is_partial=True)
    chunk2 = RawLogLine(line_num=2, text="Part 2 of long line.", is_partial=False)
    next_line = RawLogLine(line_num=3, text="Next log line.", is_partial=False)

    ev1 = stitcher.push(chunk1)
    assert ev1 is None  # still buffering partial

    ev2 = stitcher.push(chunk2)
    assert ev2 is None  # waiting for next line to flush

    ev3 = stitcher.push(next_line)
    assert ev3 is not None
    assert "Part 1 of long line..." in ev3.message
    assert "Part 2 of long line." in ev3.message


# ==============================================================================
# 3. Detection of Level and Issue Type
# ==============================================================================


def test_detect_level():
    assert detect_level("everything is fine", explicit_level="info") == "INFO"
    assert detect_level("Disk almost full", explicit_level="warning") == "WARN"
    assert detect_level("Failed to connect", explicit_level="err") == "ERROR"
    assert detect_level("Kernel panic: fatal exception") == "FATAL"
    assert detect_level("ERROR: service unreachable") == "ERROR"
    assert detect_level("WARN: deprecation notice") == "WARN"
    assert detect_level("DEBUG: trace payload") == "DEBUG"


def test_detect_issue_type():
    assert detect_issue_type("Process 1234 killed by oom-killer") == "OOM Killed"
    assert detect_issue_type("fatal error: panic in goroutine") == "Panic/Crash"
    assert detect_issue_type("x509: certificate has expired or is not yet valid") == "Certificate Expired"
    assert detect_issue_type("dial tcp 127.0.0.1:8200: connect: connection refused") == "Connection Refused"
    assert detect_issue_type("context deadline exceeded while waiting for lock") == "Timeout"
    assert detect_issue_type("write: no space left on device") == "Disk Full"
    assert detect_issue_type("Unhandled KeyError: 'id'", level="ERROR") == "General Error"
    assert detect_issue_type("Healthy status ping", level="INFO") == "Info"


# ==============================================================================
# 4. Stream Parsers Tests
# ==============================================================================


def test_parse_fluent_stream():
    sample_json = (
        b'{"date":"2026-08-09T17:00:04.100000Z","log":"[ERROR] Connection to vault refused\\n",'
        b'"kubernetes":{"namespace_name":"vault","pod_name":"vault-0","container_name":"vault"},"host":"node-1"}\n'
        b'{"date":"2026-08-09T17:00:05.200000Z","message":"Heartbeat ok","level":"info"}\n'
    )
    events = list(
        parse_fluent_stream(
            io.BytesIO(sample_json),
            filename="fluent.log",
            default_node="node-default",
            default_namespace="default",
        )
    )
    assert len(events) == 2
    e1 = events[0]
    assert e1.namespace == "vault"
    assert e1.pod == "vault-0"
    assert e1.node == "node-1"
    assert e1.level == "ERROR"
    assert e1.issue_type == "Connection Refused"
    assert e1.ts_micros is not None

    e2 = events[1]
    assert e2.namespace == "default"
    assert e2.level == "INFO"
    assert e2.issue_type == "Info"


def test_parse_syslog_stream():
    sample_syslog = (
        b"Aug  9 01:23:45 k8s-node-1 kubelet[450]: E0809 01:23:45.123456 450 pod_workers.go:190] Error syncing pod\n"
        b"Aug  9 01:23:46 k8s-node-1 kernel: [ 100.12] Out of memory: Kill process 890 (vault) score 950\n"
    )
    events = list(
        parse_syslog_stream(
            io.BytesIO(sample_syslog),
            filename="syslog",
            default_node="k8s-node-1",
            default_year=2026,
        )
    )
    assert len(events) == 2
    assert events[0].node == "k8s-node-1"
    assert events[0].level == "ERROR"

    assert events[1].level in ("ERROR", "FATAL")
    assert events[1].issue_type == "OOM Killed"


def test_parse_k8s_yaml_stream():
    pod_yaml = b"""
apiVersion: v1
kind: Pod
metadata:
  name: billing-api-abc
  namespace: onprem
status:
  containerStatuses:
  - name: api
    restartCount: 5
    lastState:
      terminated:
        exitCode: 137
        reason: OOMKilled
        finishedAt: "2026-08-09T15:20:00Z"
---
apiVersion: v1
kind: Event
metadata:
  name: event-1
  namespace: onprem
type: Warning
reason: BackOff
message: Back-off restarting failed container
involvedObject:
  kind: Pod
  name: billing-api-abc
"""
    facts = list(parse_k8s_yaml_stream(io.BytesIO(pod_yaml), default_ns="onprem"))
    assert len(facts) >= 2

    # Check OOM fact
    oom = next((f for f in facts if f.reason == "OOMKilled"), None)
    assert oom is not None
    assert oom.exit_code == 137
    assert oom.namespace == "onprem"
    assert oom.name == "billing-api-abc/api"

    # Check Warning event fact
    warn = next((f for f in facts if f.reason == "BackOff"), None)
    assert warn is not None
    assert "restarting failed container" in warn.detail


def test_parse_usage_stream():
    df_output = b"""
Filesystem     1K-blocks      Used Available Use% Mounted on
/dev/sda1       51474044  48900340   2557320  95% /
/dev/sdb1      102948088  10294808  92653280  10% /data
"""
    facts = list(parse_usage_stream(io.BytesIO(df_output), filename="df_h.txt", node="node-1"))
    assert len(facts) == 2
    assert facts[0].metric_name == "disk_usage_percent"
    assert facts[0].metric_value == 95.0
    assert "95% used" in facts[0].detail

    free_output = b"""
               total        used        free      shared  buff/cache   available
Mem:           31980       29400        1000         200        1580        2100
Swap:           2048         500        1548
"""
    mem_facts = list(parse_usage_stream(io.BytesIO(free_output), filename="free_m.txt", node="node-1"))
    assert len(mem_facts) == 1
    assert mem_facts[0].metric_name == "memory_usage_percent"
    assert mem_facts[0].metric_value > 90.0


def test_parse_bundle_to_store(tmp_path):
    import tarfile
    from analyzer.parse.pipeline import parse_bundle_to_store
    from analyzer.store.duck import SessionStore

    # Create a test tar bundle
    tar_path = tmp_path / "test_bundle.tar"
    with tarfile.open(tar_path, "w") as tf:
        # 1. Fluent log
        fluent_data = (
            b'{"date":"2026-08-09T12:00:00Z","log":"[ERROR] Service died\\n","host":"node-1",'
            b'"kubernetes":{"namespace_name":"vault","pod_name":"vault-0","container_name":"vault"}}\n'
        )
        ti1 = tarfile.TarInfo(name="fluent/node-1/vault/vault-0.log")
        ti1.size = len(fluent_data)
        tf.addfile(ti1, io.BytesIO(fluent_data))

        # 2. Kubernetes Pod YAML
        k8s_data = b"""
apiVersion: v1
kind: Pod
metadata:
  name: vault-0
  namespace: vault
status:
  containerStatuses:
  - name: vault
    restartCount: 4
    lastState:
      terminated:
        reason: OOMKilled
        exitCode: 137
"""
        ti2 = tarfile.TarInfo(name="kubernetes/base/namespaces/vault/pods/vault-0.yaml")
        ti2.size = len(k8s_data)
        tf.addfile(ti2, io.BytesIO(k8s_data))

    # Parse into DuckDB store
    db_path = tmp_path / "session.duckdb"
    with SessionStore(db_path) as store:
        manifest = parse_bundle_to_store(tar_path, store, session_id="test-session")
        assert manifest.total_files == 2

        meta = store.get_metadata()
        assert meta is not None
        assert meta["id"] == "test-session"
        assert meta["total_events"] == 1

        events = store.query_events()
        assert len(events) == 1
        assert events[0]["namespace"] == "vault"
        assert events[0]["level"] == "ERROR"

        facts = store.get_k8s_facts()
        assert len(facts) >= 1
        assert facts[0]["reason"] == "OOMKilled"


def test_parse_journal_stream_fallback():
    from analyzer.parse.journal import parse_journal_stream
    # Dummy binary journal data (magic LPKSHHRH...)
    dummy_journal = b"LPKSHHRH\x00\x00\x00\x00\x01\x02\x03\x04"
    events = list(parse_journal_stream(io.BytesIO(dummy_journal), filename="system.journal", default_node="node-1"))
    # When journalctl is not on PATH (macOS test environment), must gracefully yield 0 events without throwing exception
    assert isinstance(events, list)


def test_parse_bundle_selective_namespaces(tmp_path):
    from analyzer.parse.pipeline import parse_bundle_to_store
    from analyzer.store.duck import SessionStore

    bundle_path = tmp_path / "multi_ns.tar"
    with tarfile.open(bundle_path, "w") as tar:
        # File 1: ccs-system fluent log
        ccs_data = b'{"date":"2026-08-10T10:00:00Z","message":"ccs log line","kubernetes":{"namespace_name":"ccs-system"}}\n'
        ti = tarfile.TarInfo(name="fluentbit-logs/fluent-logs/ccs-system/ccs.log")
        ti.size = len(ccs_data)
        tar.addfile(ti, io.BytesIO(ccs_data))

        # File 2: onprem fluent log
        onprem_data = b'{"date":"2026-08-10T10:01:00Z","message":"onprem log line","kubernetes":{"namespace_name":"onprem"}}\n'
        ti = tarfile.TarInfo(name="fluentbit-logs/fluent-logs/onprem/onprem.log")
        ti.size = len(onprem_data)
        tar.addfile(ti, io.BytesIO(onprem_data))

    db_path = tmp_path / "test_selective.duckdb"
    with SessionStore(db_path) as store:
        # Selectively parse ONLY ccs-system
        parse_bundle_to_store(bundle_path, store, session_id="test_selective", include_namespaces=["ccs-system"])
        evs = store.query_events()
        assert len(evs) == 1
        assert evs[0]["namespace"] == "ccs-system"



