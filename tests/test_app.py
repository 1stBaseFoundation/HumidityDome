"""API endpoints and tick orchestration."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from humiditydome import app as app_module
from humiditydome import store
from humiditydome.models import Alert, Sample


def make_sample(node: str, ip: str, ts: float, **overrides: object) -> Sample:
    base: dict[str, object] = {
        "node": node,
        "ip": ip,
        "ts": ts,
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
        "peer_addresses": [],
        "clock_skew_s": 0.0,
    }
    base.update(overrides)
    return Sample(**base)  # type: ignore[arg-type]


def seed_addresses(node_name: str) -> list[str]:
    return [node.ip for node in app_module.cfg.nodes if node.name != node_name]


@pytest.fixture(autouse=True)
def clean_db():
    for table in ("samples", "node_state", "incidents", "daily_reports", "meta"):
        app_module.conn.execute(f"DELETE FROM {table}")
    app_module.conn.commit()
    yield


@pytest.fixture
def client(monkeypatch) -> TestClient:
    async def fake_probe(now: float) -> dict[str, Sample]:
        return {
            node.name: make_sample(
                node.name, node.ip, now, peer_addresses=seed_addresses(node.name)
            )
            for node in app_module.cfg.nodes
        }

    monkeypatch.setattr(app_module, "_probe_all", fake_probe)
    return TestClient(app_module.app)


def reset_tick() -> None:
    store.set_meta(app_module.conn, "last_tick_ts", "0")


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["nodes"] == 6


def test_tick_healthy(client: TestClient) -> None:
    reset_tick()

    response = client.post("/tick")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert len(body["nodes"]) == 6
    assert body["alerts"] == []


def test_tick_detects_unreachable(client: TestClient, monkeypatch) -> None:
    async def fake_probe(now: float) -> dict[str, Sample]:
        result: dict[str, Sample] = {}
        for node in app_module.cfg.nodes:
            if node.name == "seed-150":
                result[node.name] = make_sample(
                    node.name, node.ip, now, ok=False, error="timeout"
                )
            else:
                result[node.name] = make_sample(
                    node.name, node.ip, now, peer_addresses=seed_addresses(node.name)
                )
        return result

    monkeypatch.setattr(app_module, "_probe_all", fake_probe)

    reset_tick()
    client.post("/tick")  # first failure: debounced
    reset_tick()
    response = client.post("/tick")  # second failure: alert

    assert "unreachable" in [alert["kind"] for alert in response.json()["alerts"]]


def test_status_endpoint(client: TestClient) -> None:
    reset_tick()
    client.post("/tick")

    body = client.get("/status").json()

    assert len(body["nodes"]) == 6
    assert all(node["last_sample"] is not None for node in body["nodes"])


def test_report_force_and_fetch(client: TestClient) -> None:
    created = client.post("/report")

    assert created.status_code == 200
    date = created.json()["date"]

    fetched = client.get(f"/report?date={date}")

    assert fetched.status_code == 200
    assert "summary" in fetched.json()


def test_report_missing_returns_404(client: TestClient) -> None:
    assert client.get("/report?date=1999-01-01").status_code == 404


def test_idempotent_tick(client: TestClient) -> None:
    reset_tick()

    first = client.post("/tick").json()
    second = client.post("/tick").json()

    assert first.get("skipped") is not True
    assert second.get("skipped") is True


async def test_probe_all(monkeypatch) -> None:
    class _Response:
        status_code = 200

        def json(self) -> dict:
            return {"success": True, "state": "ready", "version": "1.4.7"}

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def get(self, url: str, timeout: float) -> _Response:
            return _Response()

    monkeypatch.setattr(app_module, "httpx", SimpleNamespace(AsyncClient=_Client))

    samples = await app_module._probe_all(1_800_000_000.0)

    assert len(samples) == 6
    assert all(sample.ok for sample in samples.values())


def test_tick_skips_missing_node(client: TestClient, monkeypatch) -> None:
    async def fake_probe(now: float) -> dict[str, Sample]:
        return {
            node.name: make_sample(
                node.name, node.ip, now, peer_addresses=seed_addresses(node.name)
            )
            for node in app_module.cfg.nodes
            if node.name != "seed-84"
        }

    monkeypatch.setattr(app_module, "_probe_all", fake_probe)
    reset_tick()

    body = client.post("/tick").json()

    assert "seed-84" not in body["nodes"]


def test_tick_sends_report_when_due(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(app_module.report, "is_report_time", lambda cfg, now=None: True)
    reset_tick()

    body = client.post("/tick").json()

    assert body["report_sent"] is True


def test_report_latest_404_when_empty(client: TestClient) -> None:
    assert client.get("/report").status_code == 404


def test_report_latest_returns_most_recent(client: TestClient) -> None:
    client.post("/report")

    response = client.get("/report")

    assert response.status_code == 200
    assert "summary" in response.json()


def test_manual_report_does_not_block_daily(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(app_module.report, "is_report_time", lambda cfg, now=None: True)

    # A manual report is sent first.
    assert client.post("/report").status_code == 200

    reset_tick()
    body = client.post("/tick").json()

    # The scheduled 09:00 summary still goes out.
    assert body["report_sent"] is True


async def test_alert_html_is_escaped(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_send(to, subject, html_body, cfg):
        captured["body"] = html_body

    monkeypatch.setattr(app_module.mail, "send_email", fake_send)
    alert = Alert(
        node="seed-x",
        kind="sync",
        severity="critical",
        title="<b>bad</b>",
        detail="<script>alert(1)</script>",
    )

    await app_module._dispatch_alerts([alert])

    assert "<script>" not in captured["body"]
    assert "&lt;script&gt;" in captured["body"]


def test_manual_report_cooldown(client: TestClient) -> None:
    first = client.post("/report")
    assert first.status_code == 200

    second = client.post("/report")
    assert second.status_code == 429


def test_healthz_reports_integrity(client: TestClient) -> None:
    body = client.get("/healthz").json()

    assert body["ok"] is True
    assert body["integrity"] == "ok"


def test_tick_503_on_integrity_failure(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(store, "check_integrity", lambda conn: "database disk image is malformed")
    reset_tick()

    response = client.post("/tick")

    assert response.status_code == 503
    assert "malformed" in response.json()["detail"]


async def test_tick_skips_when_already_running(monkeypatch) -> None:
    reset_tick()

    gate = asyncio.Event()

    async def gated_probe(now: float) -> dict[str, Sample]:
        await gate.wait()
        return {
            node.name: make_sample(
                node.name, node.ip, now, peer_addresses=seed_addresses(node.name)
            )
            for node in app_module.cfg.nodes
        }

    monkeypatch.setattr(app_module, "_probe_all", gated_probe)

    first = asyncio.create_task(app_module.run_tick())
    # Let the first tick acquire the lock and block inside the probe.
    await asyncio.sleep(0)
    second = await app_module.run_tick()

    gate.set()
    first_body = await first

    assert second.get("skipped") is True
    assert second["reason"] == "run in progress"
    assert first_body.get("skipped") is not True


async def test_tick_rolls_back_and_does_not_advance_on_failure(monkeypatch) -> None:
    reset_tick()

    async def fake_probe(now: float) -> dict[str, Sample]:
        return {
            node.name: make_sample(
                node.name, node.ip, now, peer_addresses=seed_addresses(node.name)
            )
            for node in app_module.cfg.nodes
        }

    monkeypatch.setattr(app_module, "_probe_all", fake_probe)

    real_insert = store.insert_sample
    calls = {"n": 0}

    def flaky_insert(conn, sample):
        calls["n"] += 1
        if calls["n"] == 3:  # fail partway through the node loop
            raise RuntimeError("insert blew up")
        return real_insert(conn, sample)

    monkeypatch.setattr(store, "insert_sample", flaky_insert)

    with pytest.raises(RuntimeError):
        await app_module.run_tick()

    # The whole transaction rolled back: no samples, last_tick_ts not advanced.
    assert store.get_meta(app_module.conn, "last_tick_ts") == "0"
    assert app_module.conn.execute("SELECT COUNT(*) AS c FROM samples").fetchone()["c"] == 0