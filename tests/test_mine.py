"""Unit tests for Drain template mining and EventCorrelator anomaly detection."""

from analyzer.analyze.correlate import EventCorrelator
from analyzer.mine.drain import DrainParser, mask_variables
from analyzer.parse.event import Event
from analyzer.store.duck import SessionStore


def test_mask_variables():
    raw = "User 1234 from 192.168.1.50 accessed 0x7fff5fbff880 at https://vault.local/v1/auth"
    masked = mask_variables(raw)
    assert "1234" not in masked
    assert "192.168.1.50" not in masked
    assert "0x7fff5fbff880" not in masked
    assert "https://vault.local/v1/auth" not in masked
    assert "<*>" in masked


def test_drain_clustering():
    drain = DrainParser(max_depth=4, sim_th=0.5)

    c1 = drain.match("Failed password for root from 10.0.0.1 port 2222 ssh2")
    c2 = drain.match("Failed password for admin from 10.0.0.2 port 3333 ssh2")
    c3 = drain.match("Connection timed out to 192.168.1.1:8200")

    # c1 and c2 share the same template
    assert c1.cluster_id == c2.cluster_id
    assert c1.size == 2
    assert "Failed password for" in c1.template
    assert "<*>" in c1.template

    # c3 is a different cluster
    assert c3.cluster_id != c1.cluster_id
    assert c3.size == 1

    top = drain.top_templates()
    assert len(top) == 2
    assert top[0].cluster_id == c1.cluster_id
    assert top[0].size == 2


def test_event_correlator_spikes():
    with SessionStore(":memory:") as store:
        correlator = EventCorrelator(bucket_seconds=60, min_spike_count=10)

        # Generate baseline: 2 events per minute across 10 minutes
        # Minute index * 60_000_000 micros
        events = []
        for m in range(10):
            t_base = (m + 1) * 60_000_000
            events.append(
                Event(
                    ts_micros=t_base + 1000,
                    ts_iso="iso",
                    source="fluent",
                    node="n1",
                    namespace="auth",
                    pod="p",
                    container="c",
                    level="INFO",
                    issue_type="Info",
                    message="Routine ping",
                    file="f",
                    line_num=1,
                )
            )
            events.append(
                Event(
                    ts_micros=t_base + 2000,
                    ts_iso="iso",
                    source="fluent",
                    node="n1",
                    namespace="auth",
                    pod="p",
                    container="c",
                    level="INFO",
                    issue_type="Info",
                    message="Routine ping",
                    file="f",
                    line_num=2,
                )
            )

        # Inject massive spike in minute 11: 50 error events across multiple namespaces
        spike_base = 11 * 60_000_000
        for i in range(50):
            ns = "vault" if i % 2 == 0 else "onprem"
            events.append(
                Event(
                    ts_micros=spike_base + (i * 1000),
                    ts_iso="iso",
                    source="fluent",
                    node="n1",
                    namespace=ns,
                    pod="p",
                    container="c",
                    level="ERROR",
                    issue_type="Connection Refused",
                    message=f"Connection refused to {ns}",
                    file="f",
                    line_num=100 + i,
                )
            )

        store.insert_events(events)

        anomalies = correlator.detect_spikes(store)
        assert len(anomalies) >= 1
        top_anomaly = anomalies[0]
        assert top_anomaly.event_count == 50
        assert top_anomaly.score >= 3.0
        assert "vault" in top_anomaly.namespaces
        assert "onprem" in top_anomaly.namespaces
