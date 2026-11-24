"""Fault detection, debounce, and incident transitions."""
from __future__ import annotations

from humiditydome import alerts, store
from humiditydome.config import AlertConfig
from humiditydome.models import NodeConfig, NodeState, Sample


def make_sample(**overrides: object) -> Sample:
    base: dict[str, object] = {
        "node": "seed-test",
        "ip": "1.1.1.1",
        "ts": 0.0,
        "ok": True,
        "latency_ms": 10.0,
        "state": "ready",
        "version": "1.4.7",
        "current_height": 100,
        "target_height": 100,
        "p2p_height": 100,
        "explorer_height": 100,
        "height_divergence": 0,
        "tolerance": 3,
        "is_synchronized": True,
        "peer_count": 8,
        "min_peers": 1,
        "inbound_peers": 4,
        "outbound_peers": 4,
        "peer_addresses": ["2.2.2.2"],
        "clock_skew_s": 0.0,
    }
    base.update(overrides)
    return Sample(**base)  # type: ignore[arg-type]


def test_healthy_node_has_no_alerts() -> None:
    assert alerts.evaluate(make_sample(), NodeState(node="seed-test"), AlertConfig()) == []


def test_unreachable_node_alerts() -> None:
    found = alerts.evaluate(
        make_sample(ok=False, error="ConnectError: boom"),
        NodeState(node="seed-test"),
        AlertConfig(),
    )
    assert [a.kind for a in found] == ["unreachable"]
    assert found[0].severity == "critical"


def test_unsynced_node_alerts() -> None:
    found = alerts.evaluate(
        make_sample(is_synchronized=False, sync_reason="behind"),
        NodeState(node="seed-test"),
        AlertConfig(),
    )
    assert any(a.kind == "sync" for a in found)


def test_peer_drop_alerts() -> None:
    cfg = AlertConfig(peer_drop_fraction=0.4)
    state = NodeState(node="seed-test", last_peer_count=10)
    found = alerts.evaluate(make_sample(peer_count=5), state, cfg)
    assert any(a.kind == "peers_drop" for a in found)


def test_peer_below_minimum_alerts() -> None:
    found = alerts.evaluate(
        make_sample(peer_count=0, min_peers=1),
        NodeState(node="seed-test"),
        AlertConfig(),
    )
    assert any(a.kind == "peers_low" for a in found)


def test_height_divergence_alerts() -> None:
    found = alerts.evaluate(
        make_sample(current_height=100, target_height=110, height_divergence=10),
        NodeState(node="seed-test"),
        AlertConfig(),
    )
    assert any(a.kind == "height" for a in found)


def test_clock_skew_alerts() -> None:
    found = alerts.evaluate(
        make_sample(clock_skew_s=120.0),
        NodeState(node="seed-test"),
        AlertConfig(clock_skew_seconds=30),
    )
    assert any(a.kind == "clock_skew" for a in found)


def test_debounce_requires_two_failures(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    cfg = AlertConfig(consecutive_failures=2)

    first = alerts.process_sample(
        conn, make_sample(ok=False, error="x"), store.get_node_state(conn, "seed-test"), cfg
    )
    assert first == []

    second = alerts.process_sample(
        conn,
        make_sample(ok=False, error="x"),
        store.get_node_state(conn, "seed-test"),
        cfg,
    )
    assert [a.kind for a in second] == ["unreachable"]


def test_recovery_after_incident(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    cfg = AlertConfig(consecutive_failures=1, send_recovery=True)

    alerts.process_sample(
        conn, make_sample(ok=False, error="x"), store.get_node_state(conn, "seed-test"), cfg
    )
    recovery = alerts.process_sample(
        conn, make_sample(), store.get_node_state(conn, "seed-test"), cfg
    )

    assert [a.kind for a in recovery] == ["recovery"]
    assert recovery[0].recovery is True


def test_isolated_node_alerts_once(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    nodes = [
        NodeConfig(name="seed-a", ip="1.1.1.1", port=3001),
        NodeConfig(name="seed-b", ip="2.2.2.2", port=3001),
    ]

    samples = {
        "seed-a": make_sample(node="seed-a", ip="1.1.1.1", peer_addresses=[]),
        "seed-b": make_sample(node="seed-b", ip="2.2.2.2", peer_addresses=["1.1.1.1"]),
    }

    first = alerts.fleet_alerts(conn, samples, nodes, AlertConfig())
    assert [a.kind for a in first] == ["isolated"]

    second = alerts.fleet_alerts(conn, samples, nodes, AlertConfig())
    assert second == []


def test_peer_min_override() -> None:
    found = alerts.evaluate(
        make_sample(peer_count=8),
        NodeState(node="seed-test"),
        AlertConfig(peer_min=10),
    )
    assert any(alert.kind == "peers_low" for alert in found)


def test_state_not_ready_alerts() -> None:
    found = alerts.evaluate(
        make_sample(state="syncing"),
        NodeState(node="seed-test"),
        AlertConfig(),
    )
    assert any(alert.kind == "state" for alert in found)


def test_version_change_is_info(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    state = NodeState(node="seed-test", first_seen_version="1.4.7")

    found = alerts.process_sample(
        conn,
        make_sample(version="1.4.8"),
        state,
        AlertConfig(consecutive_failures=1),
    )

    assert [alert.kind for alert in found] == ["version_change"]


def test_recovery_disabled(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    cfg = AlertConfig(consecutive_failures=1, send_recovery=False)

    alerts.process_sample(
        conn, make_sample(ok=False, error="x"), store.get_node_state(conn, "seed-test"), cfg
    )
    found = alerts.process_sample(
        conn, make_sample(), store.get_node_state(conn, "seed-test"), cfg
    )

    assert found == []


def test_cooldown_suppresses_ongoing(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    cfg = AlertConfig(consecutive_failures=1, cooldown_minutes=60)

    first = alerts.process_sample(
        conn, make_sample(ok=False, error="x"), store.get_node_state(conn, "seed-test"), cfg
    )
    second = alerts.process_sample(
        conn, make_sample(ok=False, error="x"), store.get_node_state(conn, "seed-test"), cfg
    )

    assert first and first[0].title.startswith("ALERT")
    assert second == []


def test_fleet_recovery(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    nodes = [
        NodeConfig(name="seed-a", ip="1.1.1.1", port=3001),
        NodeConfig(name="seed-b", ip="2.2.2.2", port=3001),
    ]
    samples = {
        "seed-a": make_sample(node="seed-a", ip="1.1.1.1", peer_addresses=[]),
        "seed-b": make_sample(node="seed-b", ip="2.2.2.2", peer_addresses=["1.1.1.1"]),
    }

    first = alerts.fleet_alerts(conn, samples, nodes, AlertConfig())
    assert [alert.kind for alert in first] == ["isolated"]

    samples["seed-a"].peer_addresses = ["2.2.2.2"]
    second = alerts.fleet_alerts(conn, samples, nodes, AlertConfig())

    assert [alert.kind for alert in second] == ["recovery"]


def test_new_incident_within_cooldown_is_silent(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    cfg = AlertConfig(consecutive_failures=1, cooldown_minutes=1440, send_recovery=True)

    first = alerts.process_sample(
        conn, make_sample(ok=False, error="x"), store.get_node_state(conn, "seed-test"), cfg
    )
    assert [alert.kind for alert in first] == ["unreachable"]

    # Recover.
    alerts.process_sample(
        conn, make_sample(), store.get_node_state(conn, "seed-test"), cfg
    )

    # Fail again within 24h: the incident opens, but no second alert email.
    second = alerts.process_sample(
        conn, make_sample(ok=False, error="x"), store.get_node_state(conn, "seed-test"), cfg
    )
    assert second == []