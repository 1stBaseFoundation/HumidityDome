"""Data models."""
from __future__ import annotations

import pytest

from humiditydome.models import NodeConfig, Sample


def test_node_config_url_and_label() -> None:
    node = NodeConfig(name="seed-150", ip="150.136.172.164", port=3001)

    assert node.url == "http://150.136.172.164:3001/api/node/state/status"
    assert node.label == "seed-150 (150.136.172.164)"


def test_sample_to_dict() -> None:
    sample = Sample(node="seed-a", ip="1.1.1.1", ts=1.0, ok=True)

    data = sample.to_dict()

    assert data["node"] == "seed-a"
    assert data["ok"] is True
    assert data["peer_addresses"] == []


def test_https_node_config() -> None:
    node = NodeConfig(
        name="node-1stbase",
        ip="node.1stbase.org",
        port=443,
        scheme="https",
    )

    assert node.url == "https://node.1stbase.org:443/api/node/state/status"
    assert node.seed_ip is None


def test_node_config_rejects_bad_url_parts() -> None:
    with pytest.raises(ValueError):
        NodeConfig(name="x", ip="evil.com", port=3001, path="@bad")

    with pytest.raises(ValueError):
        NodeConfig(name="x", ip="evil.com", port=3001, path="api")

    with pytest.raises(ValueError):
        NodeConfig(name="x", ip="a@b.com", port=3001)

    with pytest.raises(ValueError):
        NodeConfig(name="x", ip="evil.com", port=3001, scheme="ftp")