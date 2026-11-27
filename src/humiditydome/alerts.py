"""Fault detection, debounce, and incident state transitions."""
from __future__ import annotations

import sqlite3
import time

from . import store
from .config import AlertConfig
from .models import Alert, NodeConfig, NodeState, Sample

_SEVERITY_RANK = {"info": 1, "warning": 2, "critical": 3}
_FAULT_KINDS = {
    "unreachable",
    "sync",
    "state",
    "height",
    "peers_low",
    "peers_drop",
    "clock_skew",
}


def _min_peers(sample: Sample, cfg: AlertConfig) -> int:
    if cfg.peer_min is not None:
        return cfg.peer_min
    return sample.min_peers if sample.min_peers is not None else 1


def _baseline(version: str | None) -> str | None:
    """Strip the trailing '*' anchor used to mark an already-reported version.

    ``first_seen_version`` stores the last version we alerted on, with a '*'
    suffix once that version has been surfaced. Comparing/displaying must use the
    bare version, so the anchor is removed everywhere except the stored value.
    """
    return version.rstrip("*") if version else version


def _worst(alerts: list[Alert]) -> Alert:
    return max(alerts, key=lambda a: _SEVERITY_RANK.get(a.severity, 0))


def _details(alerts: list[Alert]) -> str:
    return " | ".join(f"{a.kind}: {a.detail}" for a in alerts)


def evaluate(sample: Sample, state: NodeState, cfg: AlertConfig) -> list[Alert]:
    """Return all alerts suggested by this sample. Does not mutate state."""
    if not sample.ok:
        return [
            Alert(
                node=sample.node,
                kind="unreachable",
                severity="critical",
                title=f"{sample.node} is unreachable",
                detail=sample.error or "request failed",
            )
        ]

    alerts: list[Alert] = []

    if sample.is_synchronized is False:
        alerts.append(
            Alert(
                node=sample.node,
                kind="sync",
                severity="critical",
                title=f"{sample.node} is not synchronized",
                detail=sample.sync_reason or f"height {sample.current_height}/{sample.target_height}",
            )
        )

    if sample.state not in (None, "ready"):
        alerts.append(
            Alert(
                node=sample.node,
                kind="state",
                severity="critical",
                title=f"{sample.node} state is {sample.state}",
                detail=sample.sync_reason or "node not ready",
            )
        )

    tolerance = sample.tolerance if sample.tolerance is not None else 3
    if sample.height_divergence is not None and sample.height_divergence > tolerance:
        alerts.append(
            Alert(
                node=sample.node,
                kind="height",
                severity="warning",
                title=f"{sample.node} height divergence {sample.height_divergence}",
                detail=(
                    f"current={sample.current_height} target={sample.target_height} "
                    f"p2p={sample.p2p_height} explorer={sample.explorer_height}"
                ),
            )
        )

    minimum = _min_peers(sample, cfg)
    if sample.peer_count is not None and sample.peer_count < minimum:
        alerts.append(
            Alert(
                node=sample.node,
                kind="peers_low",
                severity="critical",
                title=f"{sample.node} has {sample.peer_count} peers (min {minimum})",
                detail=f"inbound={sample.inbound_peers} outbound={sample.outbound_peers}",
            )
        )
    elif (
        sample.peer_count is not None
        and state.last_peer_count is not None
        and state.last_peer_count > 0
    ):
        drop = (state.last_peer_count - sample.peer_count) / state.last_peer_count
        if drop >= cfg.peer_drop_fraction:
            alerts.append(
                Alert(
                    node=sample.node,
                    kind="peers_drop",
                    severity="warning",
                    title=f"{sample.node} peer count dropped {state.last_peer_count} -> {sample.peer_count}",
                    detail=f"{drop * 100:.0f}% drop since last check",
                )
            )

    if sample.clock_skew_s is not None and abs(sample.clock_skew_s) > cfg.clock_skew_seconds:
        alerts.append(
            Alert(
                node=sample.node,
                kind="clock_skew",
                severity="warning",
                title=f"{sample.node} clock skew {sample.clock_skew_s:+.1f}s",
                detail="node timestamp differs from monitor clock",
            )
        )

    if sample.version and state.first_seen_version and sample.version != _baseline(state.first_seen_version):
        alerts.append(
            Alert(
                node=sample.node,
                kind="version_change",
                severity="info",
                title=f"{sample.node} version {_baseline(state.first_seen_version)} -> {sample.version}",
                detail="node version changed since first seen",
            )
        )

    return alerts


def process_sample(
    conn: sqlite3.Connection,
    sample: Sample,
    state: NodeState,
    cfg: AlertConfig,
) -> list[Alert]:
    """Evaluate a sample, update rolling state, and return any alerts to send."""
    now = time.time()
    alerts = evaluate(sample, state, cfg)
    state.last_check_ts = now

    is_fault = any(alert.kind in _FAULT_KINDS for alert in alerts)

    if is_fault:
        state.consecutive_failures += 1
        state.status = "down" if not sample.ok else "degraded"
    else:
        state.consecutive_failures = 0
        state.status = "up"
        if sample.ok:
            state.last_ok_ts = now
            if state.first_seen_version is None and sample.version:
                state.first_seen_version = sample.version

    if sample.peer_count is not None:
        state.last_peer_count = sample.peer_count

    cooldown_seconds = cfg.cooldown_minutes * 60

    if not is_fault:
        # Healthy. If an incident is open, this is a recovery.
        if state.open_incident_id is not None:
            incident_id = state.open_incident_id
            store.resolve_incident(conn, sample.node)
            state.open_incident_id = None
            # Do not touch last_alert_ts here: the alert window is per node, not
            # per incident, so a flap inside the window stays quiet.
            store.upsert_node_state(conn, state)
            if cfg.send_recovery:
                return [
                    Alert(
                        node=sample.node,
                        kind="recovery",
                        severity="info",
                        title=f"{sample.node} recovered",
                        detail=f"incident #{incident_id} resolved; node is healthy",
                        recovery=True,
                    )
                ]
            return []

        # No incident: surface info-level alerts (e.g. version change).
        # Anchor the baseline so a reported version change fires once, not hourly.
        if any(alert.kind == "version_change" for alert in alerts):
            state.first_seen_version = f"{sample.version}*"
        store.upsert_node_state(conn, state)
        if alerts:
            return [alert for alert in alerts if alert.severity == "info"]
        return []

    # Fault path.
    should_open = state.consecutive_failures >= cfg.consecutive_failures
    immediate = any(alert.kind == "peers_low" for alert in alerts)
    within_cooldown = (
        state.last_alert_ts is not None and (now - state.last_alert_ts) < cooldown_seconds
    )

    if (should_open or immediate) and state.open_incident_id is None:
        worst = _worst(alerts)
        incident_id = store.open_incident(
            conn, sample.node, worst.kind, worst.severity, _details(alerts)
        )
        state.open_incident_id = incident_id
        if not within_cooldown:
            state.last_alert_ts = now
            store.upsert_node_state(conn, state)
            return [Alert(**{**worst.__dict__, "title": f"ALERT: {worst.title}"})]
        # An alert for this node already went out recently; open the incident
        # silently and let the daily report cover it.
        store.upsert_node_state(conn, state)
        return []

    if state.open_incident_id is not None:
        # Incident already open: re-notify only after the cooldown (default 24h).
        if not within_cooldown:
            worst = _worst(alerts)
            state.last_alert_ts = now
            store.upsert_node_state(conn, state)
            return [Alert(**{**worst.__dict__, "title": f"ALERT (ongoing): {worst.title}"})]
        store.upsert_node_state(conn, state)
        return []

    store.upsert_node_state(conn, state)
    return []


def fleet_alerts(
    conn: sqlite3.Connection,
    samples: dict[str, Sample],
    nodes: list[NodeConfig],
    cfg: AlertConfig,
) -> list[Alert]:
    """Alert when a node has no seed peers at all (isolated from the fleet)."""
    seed_ips = {node.seed_ip for node in nodes if node.seed_ip}
    alerts: list[Alert] = []

    for node in nodes:
        if not node.seed_ip:
            continue
        sample = samples.get(node.name)
        if not sample or not sample.ok:
            continue

        seen = set(sample.peer_addresses)
        others = seed_ips - {node.seed_ip}
        isolated = not (seen & others)
        key = f"isolated:{node.name}"

        if isolated and not store.get_meta(conn, key):
            store.set_meta(conn, key, "1")
            alerts.append(
                Alert(
                    node=node.name,
                    kind="isolated",
                    severity="critical",
                    title=f"{node.name} is isolated from all other seeds",
                    detail=f"peer_count={sample.peer_count}; no seed peers among {sorted(others)}",
                )
            )
        elif not isolated and store.get_meta(conn, key):
            store.set_meta(conn, key, "")
            if cfg.send_recovery:
                alerts.append(
                    Alert(
                        node=node.name,
                        kind="recovery",
                        severity="info",
                        title=f"{node.name} is peering with other seeds again",
                        detail="seed connectivity restored",
                        recovery=True,
                    )
                )

    return alerts