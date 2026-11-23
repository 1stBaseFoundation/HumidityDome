"""Daily rollup aggregation and compactness."""
from __future__ import annotations

from humiditydome import store
from humiditydome.models import NodeConfig, Sample

NODES = [
    NodeConfig(name="seed-a", ip="1.1.1.1", port=3001),
    NodeConfig(name="seed-b", ip="2.2.2.2", port=3001),
]


def make_sample(node: str, ip: str, ts: float, **overrides: object) -> Sample:
    base: dict[str, object] = {
        "node": node,
        "ip": ip,
        "ts": ts,
        "ok": True,
        "latency_ms": 20.0,
        "state": "ready",
        "version": "1.4.7",
        "current_height": 1000,
        "target_height": 1000,
        "p2p_height": 1000,
        "explorer_height": 1000,
        "height_divergence": 0,
        "tolerance": 3,
        "is_synchronized": True,
        "peer_count": 8,
        "min_peers": 1,
        "peer_addresses": ["2.2.2.2"],
        "clock_skew_s": 0.1,
        "congestion_level": "low",
    }
    base.update(overrides)
    return Sample(**base)  # type: ignore[arg-type]


def test_rollup_aggregates_per_node(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    now = 1_800_000_000.0

    for index in range(5):
        store.insert_sample(
            conn,
            make_sample(
                "seed-a",
                "1.1.1.1",
                now - 3600 + index * 60,
                peer_count=8 + index,
                current_height=1000 + index,
            ),
        )
        store.insert_sample(
            conn,
            make_sample(
                "seed-b",
                "2.2.2.2",
                now - 3600 + index * 60,
                version="1.4.8",
                peer_addresses=["1.1.1.1"],
            ),
        )

    rollup = store.build_rollup(conn, now - 7200, now, NODES)

    assert rollup["fleet"]["nodes_total"] == 2
    assert rollup["fleet"]["nodes_currently_up"] == 2

    seed_a = next(n for n in rollup["nodes"] if n["node"] == "seed-a")
    assert seed_a["samples"] == 5
    assert seed_a["uptime_pct"] == 100.0
    assert seed_a["peer_trend"] == 4
    assert seed_a["height_start"] == 1000
    assert seed_a["height_end"] == 1004

    assert "version drift" in " ".join(rollup["fleet"]["notes"])


def test_rollup_counts_downtime(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    now = 1_800_000_000.0

    store.insert_sample(conn, make_sample("seed-a", "1.1.1.1", now - 600))
    store.insert_sample(
        conn,
        make_sample("seed-a", "1.1.1.1", now - 300, ok=False, error="timeout"),
    )

    rollup = store.build_rollup(conn, now - 3600, now, NODES)
    seed_a = next(n for n in rollup["nodes"] if n["node"] == "seed-a")

    assert seed_a["uptime_pct"] == 50.0
    assert seed_a["last_state"] == "unreachable"


def test_rollup_is_compact_enough_for_free_tier(tmp_path) -> None:
    """The rollup is what we send to the LLM; keep it well under Groq's 8K TPM."""
    import json

    conn = store.connect(str(tmp_path / "test.db"))
    now = 1_800_000_000.0

    for index in range(48):
        store.insert_sample(
            conn,
            make_sample("seed-a", "1.1.1.1", now - 3600 + index * 60, peer_count=8),
        )

    rollup = store.build_rollup(conn, now - 7200, now, NODES)
    encoded = json.dumps(rollup, separators=(",", ":"))

    # ~4 chars per token is a safe upper bound; 8K TPM is the binding limit.
    assert len(encoded) / 4 < 2000, f"rollup too large: {len(encoded)} chars"