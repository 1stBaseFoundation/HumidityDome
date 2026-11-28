"""Configuration loading and validation."""
from __future__ import annotations

import dataclasses
import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import NodeConfig


def _build[T](cls: type[T], data: dict[str, Any] | None) -> T:
    """Instantiate a dataclass from a dict, ignoring unknown keys."""
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in (data or {}).items() if k in names})


@dataclass
class PollConfig:
    interval_minutes: int = 60
    timeout_seconds: float = 10.0
    user_agent: str = "HumidityDome/1.0"


@dataclass
class ReportConfig:
    time_local: str = "09:00"
    utc_offset_hours: int = 2
    to_email: str = "info@1stbase.org"
    from_email: str = "noreply@1stbase.org"
    reply_to: str = "info@1stbase.org"

    @property
    def hour_minute(self) -> tuple[int, int]:
        hour, minute = self.time_local.split(":", 1)
        return int(hour), int(minute)


@dataclass
class AlertConfig:
    consecutive_failures: int = 2
    peer_drop_fraction: float = 0.40
    peer_min: int | None = None
    cooldown_minutes: int = 1440
    send_recovery: bool = True
    clock_skew_seconds: float = 30.0
    stale_peer_seconds: float = 86400.0


@dataclass
class LLMConfig:
    model: str = "qwen/qwen3.8-27b"
    gateway_base_url: str = ""
    temperature: float = 0.2
    max_tokens: int = 800


@dataclass
class EmailConfig:
    gateway_base_url: str = ""


@dataclass
class Config:
    poll: PollConfig
    report: ReportConfig
    alerts: AlertConfig
    llm: LLMConfig
    email: EmailConfig
    nodes: list[NodeConfig]
    status_path: str = "/api/node/state/status"
    database: str = "humiditydome.db"
    retention_days: int = 7
    dry_run: bool = False
    # Optional watchdog URL pinged after each completed tick (Healthchecks.io).
    heartbeat_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load config from YAML, with environment overrides for secrets/paths."""
    if path is None:
        path = os.environ.get("HUMIDITYDOME_CONFIG", "config/nodes.yaml")

    with Path(path).open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    status_path = raw.get("paths", {}).get("status", "/api/node/state/status")
    database = os.environ.get("HUMIDITYDOME_DB") or raw.get("paths", {}).get(
        "database", "humiditydome.db"
    )
    retention_days = int(raw.get("paths", {}).get("retention_days", 7))
    dry_run = os.environ.get("HUMIDITYDOME_DRYRUN", "").lower() in {"1", "true", "yes"}
    dry_run = dry_run or bool(raw.get("paths", {}).get("dry_run", False))
    heartbeat_url = os.environ.get("HUMIDITYDOME_HEARTBEAT_URL") or str(
        raw.get("paths", {}).get("heartbeat_url", "")
    )

    nodes: list[NodeConfig] = []
    for item in raw.get("nodes", []) or []:
        ip = str(item["ip"])
        seed_ip = item.get("seed_ip")
        if seed_ip is None:
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                seed_ip = None
            else:
                seed_ip = ip
        nodes.append(
            NodeConfig(
                name=str(item["name"]),
                ip=ip,
                port=int(item.get("port", 3001)),
                path=str(item.get("path", status_path)),
                scheme=str(item.get("scheme", "http")),
                seed_ip=seed_ip,
            )
        )

    return Config(
        poll=_build(PollConfig, raw.get("poll")),
        report=_build(ReportConfig, raw.get("report")),
        alerts=_build(AlertConfig, raw.get("alerts")),
        llm=_build(LLMConfig, raw.get("llm")),
        email=_build(EmailConfig, raw.get("email")),
        nodes=nodes,
        status_path=status_path,
        database=database,
        retention_days=retention_days,
        dry_run=dry_run,
        heartbeat_url=heartbeat_url,
        raw=raw,
    )