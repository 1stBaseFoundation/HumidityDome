"""Probe parsing against the real 1stBase node status payload."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from humiditydome.models import NodeConfig
from humiditydome.probe import _as_int, _clean_str, _parse, fetch_status

NODE = NodeConfig(name="seed-150", ip="150.136.172.164", port=3001)
NOW = datetime(2026, 11, 23, 21, 16, 0, tzinfo=UTC).timestamp()

FIXTURE = {
    "success": True,
    "state": "ready",
    "timestamp": "2026-11-23T21:16:00.277Z",
    "version": "1.4.7",
    "sync": {
        "isSynchronized": True,
        "currentHeight": 279254,
        "targetHeight": 279254,
        "p2pHeight": 279254,
        "explorerHeight": 279254,
        "tolerance": 3,
        "reason": "All primary components are within tolerance (diff: 0, max allowed: 3).",
    },
    "network": {
        "peerCount": 8,
        "minPeers": 1,
        "activeConnections": 8,
        "peers": [
            {
                "peerId": "p1",
                "address": "129.153.171.162",
                "protocol": "tcp",
                "isInbound": False,
                "lastSeen": "2026-11-18T17:11:42.223Z",
            },
            {
                "peerId": "p2",
                "address": "84.12.114.48",
                "protocol": "tcp",
                "isInbound": False,
                "lastSeen": "2026-11-18T17:11:42.508Z",
            },
            {
                "peerId": "p3",
                "address": "145.241.103.198",
                "protocol": "tcp",
                "isInbound": True,
                "lastSeen": "2026-11-23T21:15:55.572Z",
            },
        ],
    },
    "mining": {
        "canMine": True,
        "isMining": False,
        "details": {
            "capabilityEnabled": True,
            "isSynchronized": True,
            "hasEnoughPeers": True,
            "coordinatorNotPaused": True,
        },
    },
    "mempool": {
        "stats": {
            "totalCount": 0,
            "congestionLevel": "low",
            "pendingSwaps": 1,
            "cachedTransactions": 6,
        },
        "coordinator": {"cacheDepth": 0, "inflight": 0},
    },
    "capabilities": {
        "canMine": True,
        "canCreateTx": True,
        "canServeBlocks": True,
        "all": ["canMine", "canCreateTx", "canServeBlocks", "canServeMempool"],
    },
}


def test_parse_ready_node() -> None:
    sample = _parse(NODE, FIXTURE, latency_ms=12.3, now=NOW, stale_after_s=86400)

    assert sample.ok is True
    assert sample.state == "ready"
    assert sample.version == "1.4.7"
    assert sample.current_height == 279254
    assert sample.target_height == 279254
    assert sample.height_divergence == 0
    assert sample.tolerance == 3
    assert sample.is_synchronized is True
    assert sample.peer_count == 8
    assert sample.min_peers == 1
    assert sample.inbound_peers == 1
    assert sample.outbound_peers == 2
    assert sample.stale_peers == 2
    assert sample.congestion_level == "low"
    assert sample.pending_swaps == 1
    assert sample.cached_transactions == 6
    assert sample.can_serve_blocks is True
    assert sample.clock_skew_s is not None and abs(sample.clock_skew_s) < 1


@pytest.mark.asyncio
async def test_fetch_status_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "150.136.172.164"
        assert request.url.path == "/api/node/state/status"
        return httpx.Response(200, json=FIXTURE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sample = await fetch_status(
            client, NODE, timeout=5, stale_after_s=86400, now=NOW
        )

    assert sample.ok is True
    assert sample.version == "1.4.7"


@pytest.mark.asyncio
async def test_fetch_status_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sample = await fetch_status(
            client, NODE, timeout=5, stale_after_s=86400, now=NOW
        )

    assert sample.ok is False
    assert "503" in (sample.error or "")


@pytest.mark.asyncio
async def test_fetch_status_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sample = await fetch_status(
            client, NODE, timeout=5, stale_after_s=86400, now=NOW
        )

    assert sample.ok is False
    assert "ConnectError" in (sample.error or "")


def test_as_int_accepts_numbers_and_strings() -> None:
    assert _as_int(5) == 5
    assert _as_int(5.0) == 5
    assert _as_int("42") == 42
    assert _as_int(True) is None
    assert _as_int("nope") is None


@pytest.mark.asyncio
async def test_fetch_status_success_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "reason": "maintenance"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sample = await fetch_status(client, NODE, timeout=5, stale_after_s=86400, now=NOW)

    assert sample.ok is False
    assert "maintenance" in (sample.error or "")


@pytest.mark.asyncio
async def test_fetch_status_non_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sample = await fetch_status(client, NODE, timeout=5, stale_after_s=86400, now=NOW)

    assert sample.ok is False
    assert "not a JSON object" in (sample.error or "")


@pytest.mark.asyncio
async def test_fetch_status_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sample = await fetch_status(client, NODE, timeout=5, stale_after_s=86400, now=NOW)

    assert sample.ok is False
    assert "invalid JSON" in (sample.error or "")


def test_clean_str_strips_control_chars_and_truncates() -> None:
    assert _clean_str("hello\x00world") == "hello world"
    assert _clean_str("x" * 300, 10) == "x" * 10
    assert _clean_str(None) is None
    assert _clean_str("") is None