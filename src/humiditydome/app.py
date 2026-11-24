"""FastAPI app for HumidityDome.

The Sprite proxy routes its URL to this service and wakes it on each request.
The GitHub Actions cron posts to /tick every 30 minutes.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query

from . import alerts, mail, report, store
from .config import load_config
from .models import Alert, Sample
from .probe import fetch_status

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = os.environ.get(
    "HUMIDITYDOME_CONFIG", str(_REPO_ROOT / "config" / "nodes.yaml")
)

cfg = load_config(CONFIG_PATH)
conn = store.connect(cfg.database)

app = FastAPI(title="HumidityDome", version="0.1.0")


async def _probe_all(now: float) -> dict[str, Sample]:
    timeout = cfg.poll.timeout_seconds
    stale_after = cfg.alerts.stale_peer_seconds
    async with httpx.AsyncClient(
        timeout=timeout, headers={"User-Agent": cfg.poll.user_agent}
    ) as client:
        tasks = [
            fetch_status(
                client,
                node,
                timeout=timeout,
                stale_after_s=stale_after,
                now=now,
            )
            for node in cfg.nodes
        ]
        results = await asyncio.gather(*tasks)
    return {sample.node: sample for sample in results}


async def _dispatch_alerts(alerts_list: list[Alert]) -> None:
    """Send one email per node that has alerts."""
    grouped: dict[str, list[Alert]] = {}
    for alert in alerts_list:
        grouped.setdefault(alert.node, []).append(alert)

    for node, items in grouped.items():
        lines = "".join(
            f"<li><b>{alert.severity.upper()}</b> — {alert.title}: {alert.detail}</li>"
            for alert in items
        )
        body = f"<h3>HumidityDome alert — {node}</h3><ul>{lines}</ul>"
        subject = f"HumidityDome alert — {node}"
        await mail.send_email(cfg.report.to_email, subject, body, cfg)


async def run_tick(now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now

    last_tick = store.get_meta(conn, "last_tick_ts")
    interval = cfg.poll.interval_minutes * 60
    if last_tick and (now - float(last_tick)) < interval * 0.5:
        return {
            "ok": True,
            "skipped": True,
            "reason": "tick too soon",
            "last_tick": float(last_tick),
        }
    store.set_meta(conn, "last_tick_ts", str(now))
    store.prune_old_data(conn, cfg.retention_days)

    samples = await _probe_all(now)

    out_alerts: list[Alert] = []
    per_node: dict[str, Sample] = {}
    for node in cfg.nodes:
        sample = samples.get(node.name)
        if sample is None:
            continue
        store.insert_sample(conn, sample)
        state = store.get_node_state(conn, node.name)
        out_alerts.extend(alerts.process_sample(conn, sample, state, cfg.alerts))
        per_node[node.name] = sample

    out_alerts.extend(alerts.fleet_alerts(conn, per_node, cfg.nodes, cfg.alerts))
    await _dispatch_alerts(out_alerts)

    report_sent = False
    if report.is_report_time(cfg, now):
        report_date = report.local_date(cfg, now)
        since = report.local_report_ts(cfg, now)
        if not store.report_sent_since(conn, report_date, since):
            await report.build_and_send_report(conn, cfg, now)
            report_sent = True

    return {
        "ok": True,
        "tick_ts": now,
        "dry_run": cfg.dry_run,
        "nodes": {
            name: {
                "ok": sample.ok,
                "state": sample.state,
                "peers": sample.peer_count,
                "version": sample.version,
                "latency_ms": sample.latency_ms,
                "error": sample.error,
            }
            for name, sample in per_node.items()
        },
        "alerts": [alert.__dict__ for alert in out_alerts],
        "report_sent": report_sent,
    }


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "database": cfg.database,
        "nodes": len(cfg.nodes),
        "dry_run": cfg.dry_run,
    }


@app.post("/tick")
async def tick() -> dict[str, Any]:
    return await run_tick()


@app.get("/status")
def status() -> dict[str, Any]:
    nodes = []
    for node in cfg.nodes:
        sample = store.get_last_sample(conn, node.name)
        state = store.get_node_state(conn, node.name)
        nodes.append(
            {
                "node": node.name,
                "ip": node.ip,
                "state": state.status,
                "consecutive_failures": state.consecutive_failures,
                "last_sample": sample.to_dict() if sample else None,
            }
        )
    return {"nodes": nodes}


@app.get("/report")
def get_report(
    date: str | None = Query(default=None, description="Local date YYYY-MM-DD"),
) -> dict[str, Any]:
    if date is None:
        latest = store.get_latest_report(conn)
        if latest is None:
            raise HTTPException(404, "no reports yet")
        return latest
    found = store.get_report(conn, date)
    if found is None:
        raise HTTPException(404, "report not found")
    return found


@app.post("/report")
async def force_report() -> dict[str, Any]:
    return await report.build_and_send_report(conn, cfg)