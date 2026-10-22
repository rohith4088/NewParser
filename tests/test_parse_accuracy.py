"""Regression tests for the Phase 1 parser accuracy fixes.

Each test names the bug number from docs/IMPROVEMENT_LOG.md.
"""

import io
import json

from analyzer.analyze.rules import RuleEngine
from analyzer.parse.cri import split_cri
from analyzer.parse.event import detect_issue_type, detect_level
from analyzer.parse.fluent import parse_fluent_stream
from analyzer.parse.k8s_state import K8sFact, parse_k8s_yaml_stream
from analyzer.parse.stitch import LogStitcher, RawLogLine
from analyzer.parse.syslog import parse_syslog_stream
from analyzer.parse.timestamps import micros_to_iso, parse_any_ts, parse_iso_micros
from analyzer.parse.usage import MetricFact
from analyzer.store.duck import SessionStore


def _fluent(records: list[dict]) -> io.BytesIO:
    return io.BytesIO(b"".join(json.dumps(r).encode() + b"\n" for r in records))


# --- #1 undated lines inherit the previous timestamp ---------------------------


def test_undated_line_inherits_previous_timestamp():
    data = (
        b"Aug  9 01:00:00 node-1 app[1]: first\n"
        b"some undated line that is not a continuation\n"
    )
    events = list(parse_syslog_stream(io.BytesIO(data), filename="messages", default_year=2026))
    assert len(events) == 2
    assert events[0].ts_micros is not None
    assert events[1].ts_micros == events[0].ts_micros


# --- #2 fluent stitching -------------------------------------------------------


_K8S_A = {"namespace_name": "ns-a", "pod_name": "pod-a", "container_name": "c"}
_K8S_B = {"namespace_name": "ns-b", "pod_name": "pod-b", "container_name": "c"}


def test_fluent_partial_chunks_are_joined_without_newline():
    stream = _fluent([
        {"date": "2026-08-09T10:00:00Z", "log": "part one ", "logtag": "P", "kubernetes": _K8S_A},
        {"date": "2026-08-09T10:00:00Z", "log": "part two", "logtag": "F", "kubernetes": _K8S_A},
        {"date": "2026-08-09T10:00:01Z", "log": "next line", "kubernetes": _K8S_A},
    ])
    events = list(parse_fluent_stream(stream, filename="f.log"))
    assert [e.message for e in events] == ["part one part two", "next line"]
    assert events[0].ts_iso.startswith("2026-08-09T10:00:00")


def test_fluent_stack_trace_records_are_stitched():
    stream = _fluent([
        {"date": "2026-08-09T10:00:00Z", "log": "java.lang.IllegalStateException: boom", "kubernetes": _K8S_A},
        {"date": "2026-08-09T10:00:00Z", "log": "\tat com.example.Foo.bar(Foo.java:10)", "kubernetes": _K8S_A},
        {"date": "2026-08-09T10:00:00Z", "log": "\tat com.example.Main.main(Main.java:3)", "kubernetes": _K8S_A},
        {"date": "2026-08-09T10:00:05Z", "log": "recovered", "kubernetes": _K8S_A},
    ])
    events = list(parse_fluent_stream(stream, filename="f.log"))
    assert len(events) == 2
    assert "Foo.java:10" in events[0].message and "Main.java:3" in events[0].message
    assert events[0].issue_type == "General Error"
    assert events[1].message == "recovered"


def test_fluent_does_not_stitch_across_containers():
    stream = _fluent([
        {"date": "2026-08-09T10:00:00Z", "log": "Exception: in A", "kubernetes": _K8S_A},
        {"date": "2026-08-09T10:00:00Z", "log": "  indented line from B", "kubernetes": _K8S_B},
    ])
    events = list(parse_fluent_stream(stream, filename="f.log"))
    assert len(events) == 2
    by_ns = {e.namespace: e.message for e in events}
    assert by_ns["ns-a"] == "Exception: in A"
    assert by_ns["ns-b"] == "indented line from B"


def test_stitcher_partial_after_complete_event_starts_new_event():
    stitcher = LogStitcher()
    assert stitcher.push(RawLogLine(1, "complete line")) is None
    ev = stitcher.push(RawLogLine(2, "new partial ", is_partial=True))
    assert ev is not None and ev.message == "complete line"
    assert stitcher.push(RawLogLine(3, "tail")) is None
    assert stitcher.flush().message == "new partial tail"


# --- #13 numeric epoch timestamps ----------------------------------------------


def test_parse_any_ts_handles_epochs_and_iso():
    iso = parse_iso_micros("2026-08-09T17:00:04Z")
    assert parse_any_ts(1786294804) == iso  # seconds
    assert parse_any_ts(1786294804.0) == iso
    assert parse_any_ts(1786294804000) == iso  # ms
    assert parse_any_ts(1786294804000000) == iso  # us
    assert parse_any_ts(1786294804000000000) == iso  # ns
    assert parse_any_ts("1786294804") == iso
    assert parse_any_ts("2026-08-09T17:00:04Z") == iso
    assert parse_any_ts("garbage") is None
    assert parse_any_ts(None) is None
    assert parse_any_ts(True) is None


def test_fluent_float_epoch_date_does_not_crash():
    stream = _fluent([{"date": 1786294804.5, "log": "hello", "kubernetes": _K8S_A}])
    events = list(parse_fluent_stream(stream, filename="f.log"))
    assert len(events) == 1
    assert micros_to_iso(events[0].ts_micros).startswith("2026-08-09T17:00:04.5")


# --- #12 CRI container log format ----------------------------------------------


def test_split_cri():
    cri = split_cri("2026-08-09T17:00:04.123456789Z stderr P chunk")
    assert cri is not None
    assert cri.stream == "stderr" and cri.is_partial and cri.message == "chunk"
    assert split_cri("Aug  9 00:00:45 host app: msg") is None


def test_pod_log_cri_format_is_unwrapped_and_stitched():
    data = (
        b"2026-08-09T17:00:04.100000000Z stdout F ERROR request failed\n"
        b"2026-08-09T17:00:04.100000001Z stdout F Traceback (most recent call last):\n"
        b'2026-08-09T17:00:04.100000002Z stdout F   File "app.py", line 3, in <module>\n'
        b"2026-08-09T17:00:04.100000003Z stdout F ZeroDivisionError: division by zero\n"
        b"2026-08-09T17:00:05.000000000Z stdout P long line part 1 \n"
        b"2026-08-09T17:00:05.000000000Z stdout F and part 2\n"
    )
    events = list(parse_syslog_stream(io.BytesIO(data), filename="pod.log", source="pod_log"))
    messages = [e.message for e in events]
    assert messages[0] == "ERROR request failed"
    assert events[0].level == "ERROR"
    assert messages[1].startswith("Traceback") and "ZeroDivisionError" in messages[1]
    assert messages[2] == "long line part 1 and part 2"
    assert micros_to_iso(events[2].ts_micros).startswith("2026-08-09T17:00:05")
    assert not any(m.startswith(("F ", "P ")) for m in messages)


# --- #14 ISO-prefixed lines keep level word and continuation lines -------------


def test_iso_line_keeps_level_and_continuation():
    data = (
        b"2026-08-09 10:00:00,123 ERROR worker crashed\n"
        b"  retry budget exhausted\n"
        b"2026-08-09T10:00:01Z node-2 sshd[22]: Accepted publickey\n"
    )
    events = list(parse_syslog_stream(io.BytesIO(data), filename="app.log"))
    assert len(events) == 2
    assert events[0].message == "ERROR worker crashed\n  retry budget exhausted"
    assert events[0].level == "ERROR"
    assert events[1].node == "node-2"
    assert events[1].message == "Accepted publickey"


def _stitch(lines: list[str]) -> list[str]:
    stitcher = LogStitcher()
    out = [ev.message for i, line in enumerate(lines, 1) if (ev := stitcher.push(RawLogLine(i, line)))]
    final = stitcher.flush()
    return out + ([final.message] if final else [])


def test_python_traceback_does_not_swallow_following_lines():
    # No timestamps (as after stripping CRI/Fluent wrappers)
    events = _stitch([
        "Traceback (most recent call last):",
        '  File "app.py", line 3, in <module>',
        "ZeroDivisionError: division by zero",
        "worker restarted",
        "serving on :8080",
    ])
    assert len(events) == 3
    assert events[0].endswith("ZeroDivisionError: division by zero")
    assert events[1:] == ["worker restarted", "serving on :8080"]


def test_go_panic_is_one_event():
    events = _stitch([
        "panic: runtime error: invalid memory address or nil pointer dereference",
        "[signal SIGSEGV: segmentation violation code=0x1 addr=0x0 pc=0x4a2b3c]",
        "goroutine 1 [running]:",
        "main.(*Server).Run(0xc000123456)",
        "\t/app/server.go:42 +0x1d",
        "main.main()",
        "\t/app/main.go:10 +0x25",
        "listening again",
    ])
    assert len(events) == 2
    assert "main.go:10" in events[0]
    assert events[1] == "listening again"


def test_java_trace_does_not_swallow_following_lines():
    events = _stitch([
        "java.lang.IllegalStateException: boom",
        "\tat com.example.Foo.bar(Foo.java:10)",
        "Caused by: java.io.IOException: reset",
        "\t... 3 more",
        "request handled",
    ])
    assert len(events) == 2
    assert events[1] == "request handled"


# --- #5 waiting reasons / #6 event counts --------------------------------------


_POD_WAITING = b"""
apiVersion: v1
kind: Pod
metadata: {name: web-1, namespace: shop}
status:
  containerStatuses:
  - name: app
    restartCount: 0
    state:
      waiting: {reason: ImagePullBackOff, message: "Back-off pulling image registry/app:1.2"}
  - name: sidecar
    restartCount: 12
    state:
      waiting: {reason: CrashLoopBackOff}
  - name: stable
    restartCount: 7
    state:
      running: {startedAt: "2026-08-09T10:00:00Z"}
"""


def test_pod_waiting_reasons_and_restart_flag():
    facts = list(parse_k8s_yaml_stream(io.BytesIO(_POD_WAITING)))
    reasons = {(f.name, f.reason) for f in facts}
    assert ("web-1/app", "ImagePullBackOff") in reasons
    assert ("web-1/sidecar", "CrashLoopBackOff") in reasons
    # A real CrashLoopBackOff is not double-reported as HighRestartCount
    assert ("web-1/sidecar", "HighRestartCount") not in reasons
    # Running container with many restarts is flagged, but not as CrashLoopBackOff
    assert ("web-1/stable", "HighRestartCount") in reasons
    assert ("web-1/stable", "CrashLoopBackOff") not in reasons
    img = next(f for f in facts if f.reason == "ImagePullBackOff")
    assert "registry/app:1.2" in img.detail


_EVENTS = b"""
apiVersion: v1
kind: Event
metadata: {name: e1, namespace: shop}
type: Warning
reason: BackOff
message: Back-off restarting failed container
count: 250
firstTimestamp: "2026-08-09T09:00:00Z"
lastTimestamp: "2026-08-09T10:00:00Z"
involvedObject: {kind: Pod, name: web-1}
---
apiVersion: events.k8s.io/v1
kind: Event
metadata: {name: e2, namespace: shop}
type: Warning
reason: FailedMount
note: MountVolume.SetUp failed
regarding: {kind: Pod, name: web-2}
series: {count: 4, lastObservedTime: "2026-08-09T10:05:00Z"}
"""


def test_event_count_and_events_v1_fields():
    facts = list(parse_k8s_yaml_stream(io.BytesIO(_EVENTS)))
    backoff = next(f for f in facts if f.reason == "BackOff")
    assert backoff.occurrences == 250
    assert micros_to_iso(backoff.ts_micros).startswith("2026-08-09T10:00:00")
    assert "x250" in backoff.detail
    mount = next(f for f in facts if f.reason == "FailedMount")
    assert mount.occurrences == 4
    assert mount.name == "web-2"
    assert "MountVolume" in mount.detail


def test_malformed_yaml_document_does_not_drop_others():
    data = _EVENTS + b"\n---\nkind: Event\n  bad: [indent\n---\n" + _POD_WAITING
    facts = list(parse_k8s_yaml_stream(io.BytesIO(data)))
    reasons = {f.reason for f in facts}
    assert {"BackOff", "FailedMount", "ImagePullBackOff"} <= reasons


def test_occurrences_stored_and_counted_by_rules(tmp_path):
    (tmp_path / "r.yaml").write_text(
        "- id: BACKOFF\n  title: Backoff\n  severity: HIGH\n  target: k8s_facts\n"
        "  condition: reason = 'BackOff'\n",
        encoding="utf-8",
    )
    engine = RuleEngine(tmp_path)
    with SessionStore(":memory:") as store:
        store.insert_k8s_facts(parse_k8s_yaml_stream(io.BytesIO(_EVENTS)))
        assert store.get_k8s_facts(reason="BackOff")[0]["occurrences"] == 250
        findings = engine.evaluate(store)
        assert findings[0].count == 250


# --- #8 undated snapshot rows survive time windows -----------------------------


def test_time_window_keeps_undated_snapshot_rows(tmp_path):
    (tmp_path / "r.yaml").write_text(
        "- id: DISK\n  title: Disk\n  severity: HIGH\n  target: metrics\n"
        "  condition: metric_name = 'disk_usage_percent' AND metric_value >= 90\n"
        "- id: OOM\n  title: OOM\n  severity: HIGH\n  target: k8s_facts\n"
        "  condition: reason = 'OOMKilled'\n",
        encoding="utf-8",
    )
    engine = RuleEngine(tmp_path)
    with SessionStore(":memory:") as store:
        store.insert_metrics([MetricFact(None, "node-1", "disk_usage_percent", 95.0, "%", "/ 95%")])
        store.insert_k8s_facts([
            K8sFact(None, "Pod", "ns", "p/c", "OOMKilled", 137, 1, "undated"),
            K8sFact(10_000, "Pod", "ns", "p/c", "OOMKilled", 137, 1, "outside window"),
        ])
        findings = {f.rule_id: f for f in engine.evaluate(store, from_micros=1, to_micros=5_000)}
        assert "DISK" in findings
        # Undated fact kept, dated fact outside the window still excluded
        assert findings["OOM"].count == 1


# --- #10 benign error phrases --------------------------------------------------


def test_benign_error_phrases_are_not_errors():
    for line in (
        "sync complete, errors=0 warnings=0",
        "backup finished with no errors",
        'level=info msg="reconciled" err=<nil>',
        '{"msg": "ok", "error": null}',
        "checks passed: failed: 0, total: 12",
        "completed without any errors",
        "configured failover peer node-2",
    ):
        level = detect_level(line)
        assert level != "ERROR", line
        assert detect_issue_type(line, level) == "Info", line


def test_real_errors_still_detected():
    assert detect_level("ERROR: database unreachable") == "ERROR"
    assert detect_issue_type("caught NullPointerException in handler") == "General Error"
    assert detect_issue_type("request failed after 3 retries") == "General Error"
    assert detect_issue_type("errors=5 while syncing") == "General Error"
