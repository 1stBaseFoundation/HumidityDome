"""Data models for HumidityDome."""
from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class NodeConfig:
    """A monitored node (seed or public RPC endpoint)."""

    name: str
    ip: str
    port: int = 443
    path: str = "/api/node/state/status"
    scheme: str = "http"
    seed_ip: str | None = None

    def __post_init__(self) -> None:
        if self.scheme not in {"http", "https"}:
            raise ValueError(f"scheme must be http or https: {self.scheme!r}")
        if not (0 < self.port < 65536):
            raise ValueError(f"port out of range: {self.port}")
        if not self.path.startswith("/"):
            raise ValueError(f"path must start with '/': {self.path!r}")
        if "@" in self.path or "://" in self.path:
            raise ValueError(f"path contains invalid characters: {self.path!r}")
        if "@" in self.ip or "/" in self.ip or "://" in self.ip:
            raise ValueError(f"ip contains invalid characters: {self.ip!r}")

        if self.seed_ip is None:
            try:
                ipaddress.ip_address(self.ip)
            except ValueError:
                self.seed_ip = None
            else:
                self.seed_ip = self.ip

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.ip}:{self.port}{self.path}"

    @property
    def label(self) -> str:
        return f"{self.name} ({self.ip})"


@dataclass
class Sample:
    """One probe result for one node at one point in time."""

    node: str
    ip: str
    ts: float
    ok: bool

    latency_ms: float | None = None
    state: str | None = None
    version: str | None = None

    current_height: int | None = None
    target_height: int | None = None
    p2p_height: int | None = None
    explorer_height: int | None = None
    height_divergence: int | None = None
    tolerance: int | None = None
    is_synchronized: bool | None = None
    sync_reason: str | None = None

    peer_count: int | None = None
    min_peers: int | None = None
    active_connections: int | None = None
    inbound_peers: int | None = None
    outbound_peers: int | None = None
    stale_peers: int | None = None
    peer_addresses: list[str] = field(default_factory=list)

    congestion_level: str | None = None
    pending_swaps: int | None = None
    cached_transactions: int | None = None

    can_mine: bool | None = None
    can_serve_blocks: bool | None = None
    can_create_tx: bool | None = None

    clock_skew_s: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NodeState:
    """Rolling state for one node, used for debounce and transitions."""

    node: str
    status: str = "unknown"  # unknown | up | degraded | down
    consecutive_failures: int = 0
    last_ok_ts: float | None = None
    last_check_ts: float | None = None
    last_alert_ts: float | None = None
    open_incident_id: int | None = None
    last_peer_count: int | None = None
    first_seen_version: str | None = None


@dataclass
class Alert:
    """A single alert candidate produced by the evaluator."""

    node: str
    kind: str
    severity: str  # critical | warning | info
    title: str
    detail: str
    recovery: bool = False