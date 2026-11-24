"""HTTP probe for 1stBase seed nodes."""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

import httpx

from .models import NodeConfig, Sample

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _clean_str(value: Any, max_len: int = 200) -> str | None:
    """Strip control characters, collapse whitespace, and truncate."""
    if not isinstance(value, str):
        return None
    cleaned = _CONTROL_CHARS.sub(" ", value).strip()
    return cleaned[:max_len] if cleaned else None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _parse_ts(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _is_stale(last_seen: Any, now: float, stale_after: float) -> bool:
    ts = _parse_ts(last_seen)
    return ts is not None and (now - ts) > stale_after


def _parse(
    node: NodeConfig,
    data: dict[str, Any],
    latency_ms: float,
    now: float,
    stale_after_s: float,
) -> Sample:
    sync = data.get("sync") or {}
    network = data.get("network") or {}
    stats = (data.get("mempool") or {}).get("stats") or {}
    caps = data.get("capabilities") or {}

    peers = [p for p in (network.get("peers") or []) if isinstance(p, dict)]
    peer_addresses = [
        cleaned
        for p in peers
        if p.get("address") and (cleaned := _clean_str(str(p["address"]), 100))
    ]
    inbound = sum(1 for p in peers if p.get("isInbound"))
    outbound = len(peers) - inbound
    stale = sum(1 for p in peers if _is_stale(p.get("lastSeen"), now, stale_after_s))

    heights = [
        _as_int(sync.get("currentHeight")),
        _as_int(sync.get("targetHeight")),
        _as_int(sync.get("p2pHeight")),
        _as_int(sync.get("explorerHeight")),
    ]
    heights = [h for h in heights if h is not None]
    divergence = (max(heights) - min(heights)) if heights else None

    node_ts = _parse_ts(data.get("timestamp"))
    clock_skew = (node_ts - now) if node_ts is not None else None

    return Sample(
        node=node.name,
        ip=node.ip,
        ts=now,
        ok=True,
        latency_ms=round(latency_ms, 1),
        state=_clean_str(data.get("state"), 50),
        version=_clean_str(data.get("version"), 50),
        current_height=_as_int(sync.get("currentHeight")),
        target_height=_as_int(sync.get("targetHeight")),
        p2p_height=_as_int(sync.get("p2pHeight")),
        explorer_height=_as_int(sync.get("explorerHeight")),
        height_divergence=divergence,
        tolerance=_as_int(sync.get("tolerance")),
        is_synchronized=_as_bool(sync.get("isSynchronized")),
        sync_reason=_clean_str(sync.get("reason"), 200),
        peer_count=_as_int(network.get("peerCount")),
        min_peers=_as_int(network.get("minPeers")),
        active_connections=_as_int(network.get("activeConnections")),
        inbound_peers=inbound,
        outbound_peers=outbound,
        stale_peers=stale,
        peer_addresses=peer_addresses,
        congestion_level=_clean_str(stats.get("congestionLevel"), 50),
        pending_swaps=_as_int(stats.get("pendingSwaps")),
        cached_transactions=_as_int(stats.get("cachedTransactions")),
        can_mine=_as_bool(caps.get("canMine")),
        can_serve_blocks=_as_bool(caps.get("canServeBlocks")),
        can_create_tx=_as_bool(caps.get("canCreateTx")),
        clock_skew_s=round(clock_skew, 1) if clock_skew is not None else None,
    )


async def fetch_status(
    client: httpx.AsyncClient,
    node: NodeConfig,
    *,
    timeout: float,
    stale_after_s: float,
    now: float | None = None,
) -> Sample:
    """Fetch and parse one node's status endpoint."""
    now = time.time() if now is None else now
    start = time.monotonic()

    try:
        response = await client.get(node.url, timeout=timeout)
        latency_ms = (time.monotonic() - start) * 1000.0
    except Exception as exc:  # noqa: BLE001 - any transport failure is a failed probe
        latency_ms = (time.monotonic() - start) * 1000.0
        return Sample(
            node=node.name,
            ip=node.ip,
            ts=now,
            ok=False,
            latency_ms=round(latency_ms, 1),
            error=f"{type(exc).__name__}: {_clean_str(str(exc), 200) or ''}",
        )

    if response.status_code != 200:
        return Sample(
            node=node.name,
            ip=node.ip,
            ts=now,
            ok=False,
            latency_ms=round(latency_ms, 1),
            error=f"HTTP {response.status_code}",
        )

    try:
        data = response.json()
    except ValueError as exc:
        return Sample(
            node=node.name,
            ip=node.ip,
            ts=now,
            ok=False,
            latency_ms=round(latency_ms, 1),
            error=f"invalid JSON: {_clean_str(str(exc), 200) or ''}",
        )

    if isinstance(data, dict) and data.get("success") is False:
        return Sample(
            node=node.name,
            ip=node.ip,
            ts=now,
            ok=False,
            latency_ms=round(latency_ms, 1),
            state=_clean_str(data.get("state"), 50),
            error=_clean_str(
                str(data.get("reason") or data.get("error") or "success=false"), 200
            ),
        )

    if not isinstance(data, dict):
        return Sample(
            node=node.name,
            ip=node.ip,
            ts=now,
            ok=False,
            latency_ms=round(latency_ms, 1),
            error="response is not a JSON object",
        )

    return _parse(node, data, latency_ms, now, stale_after_s)