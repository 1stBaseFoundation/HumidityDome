"""Failure-to-ping watchdog (dead-man's switch).

The Sprite sleeps when idle, so it cannot notice its own silence: if no tick
runs, nothing inside the Sprite will ever say so. Pinging an external watchdog
after every completed tick flips the problem around — the watchdog alerts when
the pings stop arriving.

The URL is configuration, not a credential: a successful ping only means "a
tick finished", so it is safe to keep in `config/nodes.yaml`.
"""
from __future__ import annotations

import httpx

from .logging_setup import get_logger, kv

logger = get_logger("humiditydome.heartbeat")


async def ping(url: str, *, ok: bool = True, timeout: float = 10.0) -> bool:
    """Ping the watchdog; return True when the ping was accepted.

    Best-effort by design: a watchdog that is down must never fail a tick, so
    every request error is logged and swallowed. Healthchecks.io accepts
    ``<url>/fail`` to record a failed run explicitly.
    """
    if not url:
        return False

    target = url if ok else f"{url.rstrip('/')}/fail"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(target)
            response.raise_for_status()
    except Exception as exc:  # the watchdog is never allowed to break a tick
        logger.warning("heartbeat %s", kv(phase="ping_failed", error=str(exc)))
        return False

    logger.info("heartbeat %s", kv(phase="pinged", ok=ok))
    return True
