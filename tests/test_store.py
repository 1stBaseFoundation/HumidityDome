"""SQLite persistence."""
from __future__ import annotations

from humiditydome import store
from humiditydome.models import NodeConfig, Sample


def make_sample(node: str = "a", ip: str = "1.1.1.1", ts: float = 1.0, **overrides: object) -> Sample:
    base: dict[str, object] = {
        "node": node,
        "ip": ip,
        "ts": ts,
        "ok": True,
        "latency_ms": 10.0,
        "version": "1.4.7",
        "peer_addresses": [],
    }
    base.update(overrides)
    return Sample(**base)  # type: ignore[arg-type]


def test_insert_and_get_last_sample(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    store.insert_sample(conn, make_sample(ts=1.0))
    store.insert_sample(conn, make_sample(ts=2.0, version="1.4.8"))

    last = store.get_last_sample(conn, "a")

    assert last is not None
    assert last.ts == 2.0
    assert last.version == "1.4.8"


def test_samples_since_with_until(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    for index in range(3):
        store.insert_sample(conn, make_sample(ts=float(index)))

    rows = store.samples_since(conn, 0.0, 1.0)

    assert len(rows) == 2


def test_report_bookkeeping(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))

    assert store.was_report_sent(conn, "2026-11-23") is False

    store.mark_report_sent(conn, "2026-11-23", "summary")

    assert store.was_report_sent(conn, "2026-11-23") is True
    assert store.get_report(conn, "2026-11-23")["summary"] == "summary"
    assert store.get_latest_report(conn)["date"] == "2026-11-23"


def test_meta_roundtrip(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))

    assert store.get_meta(conn, "missing") is None

    store.set_meta(conn, "key", "value")

    assert store.get_meta(conn, "key") == "value"


def test_rollup_with_no_data(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    nodes = [NodeConfig(name="a", ip="1.1.1.1", port=1)]

    rollup = store.build_rollup(conn, 0.0, 10.0, nodes)

    assert rollup["nodes"][0]["samples"] == 0
    assert any("no samples" in note for note in rollup["fleet"]["notes"])


def test_rollup_single_sample_percentile(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    store.insert_sample(conn, make_sample(ts=5.0, latency_ms=20.0))
    nodes = [NodeConfig(name="a", ip="1.1.1.1", port=1)]

    rollup = store.build_rollup(conn, 0.0, 10.0, nodes)

    assert rollup["nodes"][0]["lat_p95_ms"] == 20.0