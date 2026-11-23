"""Configuration loading."""
from __future__ import annotations

from pathlib import Path

from humiditydome.config import load_config

CONFIG = Path(__file__).resolve().parents[1] / "config" / "nodes.yaml"


def test_loads_seed_nodes_and_public_endpoint() -> None:
    cfg = load_config(CONFIG)

    assert len(cfg.nodes) == 6
    assert cfg.nodes[0].ip == "150.136.172.164"
    assert cfg.nodes[0].url.endswith("/api/node/state/status")

    node = cfg.nodes[-1]
    assert node.name == "node-1stbase"
    assert node.scheme == "https"
    assert node.url == "https://node.1stbase.org:443/api/node/state/status"
    assert node.seed_ip is None


def test_report_and_alert_defaults(monkeypatch) -> None:
    monkeypatch.delenv("HUMIDITYDOME_DRYRUN", raising=False)
    cfg = load_config(CONFIG)

    assert cfg.report.utc_offset_hours == 2
    assert cfg.report.hour_minute == (7, 0)
    assert cfg.report.to_email == "info@1stbase.org"
    assert cfg.report.from_email == "noreply@1stbase.org"
    assert cfg.report.reply_to == "info@1stbase.org"
    assert cfg.alerts.consecutive_failures == 2
    assert cfg.alerts.send_recovery is True
    assert cfg.llm.model == "qwen/qwen3.8-27b"
    assert cfg.dry_run is False