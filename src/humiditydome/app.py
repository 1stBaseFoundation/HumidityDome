"""FastAPI app for HumidityDome.

The Sprite proxy routes its URL to this service and wakes it on each request.
An external scheduler (Cloudflare cron, GitHub Actions or cron-job.org) posts to
/tick every hour, and a completed tick pings an optional watchdog.
"""
from __future__ import annotations

import asyncio
import html
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query

from . import alerts, heartbeat, mail, report, store
from .config import load_config
from .logging_setup import configure_logging, get_logger, kv
from .models import Alert, Sample
from .probe import fetch_status

configure_logging()
logger = get_logger("humiditydome.app")

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = os.environ.get(
    "HUMIDITYDOME_CONFIG", str(_REPO_ROOT / "config" / "nodes.yaml")
)

cfg = load_config(CONFIG_PATH)
conn = store.connect(cfg.database)

logger.info(
    "startup %s",
    kv(
        database=cfg.database,
        nodes=len(cfg.nodes),
        dry_run=cfg.dry_run,
        interval_minutes=cfg.poll.interval_minutes,
    ),
)

app = FastAPI(title="HumidityDome", version="0.1.0")

# Single-flight guard: only one tick runs at a time in this process. In-memory
# only — a restart clears it, nothing to hang across a dirty shutdown.
_tick_lock = asyncio.Lock()


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
            f"<li><b>{html.escape(alert.severity.upper())}</b> — "
            f"{html.escape(alert.title)}: {html.escape(alert.detail)}</li>"
            for alert in items
        )
        body = (
            f"<h3>HumidityDome alert — {html.escape(node)}</h3>"
            f"<ul>{lines}</ul>"
        )
        subject = f"HumidityDome alert — {node}"
        await mail.send_email(cfg.report.to_email, subject, body, cfg)


async def run_tick(now: float | None = None) -> dict[str, Any]:
    if _tick_lock.locked():
        logger.info("tick %s", kv(phase="skipped", reason="run in progress"))
        return {"ok": True, "skipped": True, "reason": "run in progress"}
    async with _tick_lock:
        return await _run_tick_locked(now)


async def _run_tick_locked(now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    run_id = uuid.uuid4().hex[:8]
    started = time.monotonic()
    logger.info("tick %s", kv(run_id=run_id, phase="begin", now=round(now, 3)))

    last_tick = store.get_meta(conn, "last_tick_ts")
    interval = cfg.poll.interval_minutes * 60
    if last_tick and (now - float(last_tick)) < interval * 0.5:
        logger.info(
            "tick %s",
            kv(
                run_id=run_id,
                phase="skipped",
                reason="tick too soon",
                last_tick=float(last_tick),
            ),
        )
        return {
            "ok": True,
            "skipped": True,
            "reason": "tick too soon",
            "last_tick": float(last_tick),
        }

    logger.info("tick %s", kv(run_id=run_id, phase="probe_begin", nodes=len(cfg.nodes)))
    samples = await _probe_all(now)
    ok_count = sum(1 for sample in samples.values() if sample.ok)
    for sample in samples.values():
        if not sample.ok:
            logger.warning(
                "tick %s",
                kv(
                    run_id=run_id,
                    phase="probe_failed",
                    node=sample.node,
                    latency_ms=sample.latency_ms,
                    error=sample.error,
                ),
            )
    logger.info(
        "tick %s",
        kv(run_id=run_id, phase="probe_done", ok=ok_count, total=len(samples)),
    )

    # All DB writes for the tick happen in one transaction. On a dirty shutdown
    # mid-tick the WAL rolls the whole thing back, including last_tick_ts (written
    # last), so the next run re-executes instead of skipping. No network I/O here.
    out_alerts: list[Alert] = []
    per_node: dict[str, Sample] = {}
    logger.info("tick %s", kv(run_id=run_id, phase="txn_begin"))
    with store.transaction(conn):
        store.prune_old_data(conn, cfg.retention_days)
        for node in cfg.nodes:
            sample = samples.get(node.name)
            if sample is None:
                continue
            store.insert_sample(conn, sample)
            logger.debug(
                "tick %s",
                kv(run_id=run_id, phase="insert", node=node.name, ok=sample.ok),
            )
            state = store.get_node_state(conn, node.name)
            out_alerts.extend(alerts.process_sample(conn, sample, state, cfg.alerts))
            per_node[node.name] = sample

        out_alerts.extend(alerts.fleet_alerts(conn, per_node, cfg.nodes, cfg.alerts))
        logger.info(
            "tick %s",
            kv(run_id=run_id, phase="last_tick_write", old=last_tick, new=round(now, 3)),
        )
        store.set_meta(conn, "last_tick_ts", str(now))
    logger.info(
        "tick %s",
        kv(run_id=run_id, phase="txn_commit", alerts=len(out_alerts)),
    )

    # Best-effort side effects run after the commit, outside the write lock.
    await _dispatch_alerts(out_alerts)

    report_sent = False
    if report.is_report_time(cfg, now):
        report_date = report.local_date(cfg, now)
        if not store.scheduled_report_sent(conn, report_date):
            logger.info("tick %s", kv(run_id=run_id, phase="report_begin"))
            await report.build_and_send_report(conn, cfg, now)
            report_sent = True

    # The dead-man's switch: the Sprite cannot report its own silence, so the
    # watchdog is pinged only after the tick has fully completed.
    heartbeat_ok = await heartbeat.ping(cfg.heartbeat_url)

    logger.info(
        "tick %s",
        kv(
            run_id=run_id,
            phase="done",
            dur_ms=round((time.monotonic() - started) * 1000, 1),
            nodes=len(per_node),
            alerts=len(out_alerts),
            report_sent=report_sent,
            heartbeat=heartbeat_ok,
        ),
    )
    return {
        "ok": True,
        "tick_ts": now,
        "dry_run": cfg.dry_run,
        "heartbeat": heartbeat_ok,
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
    integrity = store.check_integrity(conn)
    return {
        "ok": integrity == "ok",
        "database": cfg.database,
        "nodes": len(cfg.nodes),
        "dry_run": cfg.dry_run,
        "integrity": integrity,
    }


@app.post("/tick")
async def tick() -> dict[str, Any]:
    logger.info("tick %s", kv(phase="http_request", endpoint="/tick"))
    integrity = store.check_integrity(conn)
    if integrity != "ok":
        logger.error("tick %s", kv(phase="integrity_failed", detail=integrity))
        raise HTTPException(status_code=503, detail=f"database integrity check failed: {integrity}")
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
    last = store.get_meta(conn, "last_manual_report_ts")
    if last and (time.time() - float(last)) < 300:
        raise HTTPException(429, "manual report cooldown (5 min)")
    result = await report.build_and_send_report(conn, cfg, kind="manual")
    store.set_meta(conn, "last_manual_report_ts", str(time.time()))
    return result