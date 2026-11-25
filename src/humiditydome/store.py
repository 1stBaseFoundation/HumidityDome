"""SQLite persistence for HumidityDome.

The database lives on the Sprite's persistent ext4 disk, so it survives every
warm/cold pause. WAL mode keeps writes safe if the VM is suspended mid-tick.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import NodeConfig, NodeState, Sample

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    node TEXT NOT NULL,
    ip TEXT,
    ok INTEGER NOT NULL,
    latency_ms REAL,
    state TEXT,
    version TEXT,
    current_height INTEGER,
    target_height INTEGER,
    p2p_height INTEGER,
    explorer_height INTEGER,
    height_divergence INTEGER,
    tolerance INTEGER,
    is_synchronized INTEGER,
    sync_reason TEXT,
    peer_count INTEGER,
    min_peers INTEGER,
    active_connections INTEGER,
    inbound_peers INTEGER,
    outbound_peers INTEGER,
    stale_peers INTEGER,
    peer_addresses_json TEXT,
    congestion_level TEXT,
    pending_swaps INTEGER,
    cached_transactions INTEGER,
    can_mine INTEGER,
    can_serve_blocks INTEGER,
    can_create_tx INTEGER,
    clock_skew_s REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_samples_node_ts ON samples(node, ts);

CREATE TABLE IF NOT EXISTS node_state (
    node TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'unknown',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_ok_ts REAL,
    last_check_ts REAL,
    last_alert_ts REAL,
    open_incident_id INTEGER,
    last_peer_count INTEGER,
    first_seen_version TEXT
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    opened_ts REAL NOT NULL,
    resolved_ts REAL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_node ON incidents(node, opened_ts);

CREATE TABLE IF NOT EXISTS daily_reports (
    date TEXT NOT NULL,
    kind TEXT NOT NULL,
    sent_ts REAL NOT NULL,
    summary TEXT,
    PRIMARY KEY (date, kind)
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _b(value: bool | None) -> int | None:
    return None if value is None else (1 if value else 0)


def _ub(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * (percentile / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return values[int(k)]
    return values[lo] * (hi - k) + values[hi] * (k - lo)


def connect(path: str) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Depth of active transaction() blocks, keyed by connection id. Used to defer
# inner commit() calls to the enclosing block so a whole tick is atomic.
_tx_depth: dict[int, int] = {}


def _in_transaction(conn: sqlite3.Connection) -> bool:
    return _tx_depth.get(id(conn), 0) > 0


def commit(conn: sqlite3.Connection) -> None:
    """Commit unless inside a transaction() block (deferred to that block)."""
    if not _in_transaction(conn):
        conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Group writes into one atomic unit.

    On a clean exit the block commits once; on any error it rolls back. Nested
    blocks join the outer transaction. With ``isolation_level=None`` the driver
    never issues implicit BEGINs, so this is the sole transaction boundary.
    """
    key = id(conn)
    if _tx_depth.get(key, 0) > 0:
        _tx_depth[key] += 1
        try:
            yield
        finally:
            _tx_depth[key] -= 1
        return

    conn.execute("BEGIN IMMEDIATE")
    _tx_depth[key] = 1
    try:
        yield
    except BaseException:
        _tx_depth[key] = 0
        conn.rollback()
        raise
    else:
        _tx_depth[key] = 0
        conn.commit()


def check_integrity(conn: sqlite3.Connection) -> str:
    """Return 'ok' or a description of the first integrity problem."""
    row = conn.execute("PRAGMA quick_check").fetchone()
    result = row[0] if row else "ok"
    return "ok" if result == "ok" else str(result)


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring older daily_reports tables up to the (date, kind) schema."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(daily_reports)")}
    if "kind" not in cols:
        conn.execute(
            "ALTER TABLE daily_reports ADD COLUMN kind TEXT NOT NULL DEFAULT 'scheduled'"
        )
        commit(conn)

    pk = [
        row["name"]
        for row in conn.execute("PRAGMA table_info(daily_reports)")
        if row["pk"]
    ]
    if pk == ["date"]:
        conn.executescript(
            """
            CREATE TABLE daily_reports_new (
                date TEXT NOT NULL,
                kind TEXT NOT NULL,
                sent_ts REAL NOT NULL,
                summary TEXT,
                PRIMARY KEY (date, kind)
            );
            INSERT INTO daily_reports_new (date, kind, sent_ts, summary)
                SELECT date, kind, sent_ts, summary FROM daily_reports;
            DROP TABLE daily_reports;
            ALTER TABLE daily_reports_new RENAME TO daily_reports;
            """
        )
        commit(conn)


def _row_to_sample(row: sqlite3.Row) -> Sample:
    return Sample(
        node=row["node"],
        ip=row["ip"] or "",
        ts=row["ts"],
        ok=bool(row["ok"]),
        latency_ms=row["latency_ms"],
        state=row["state"],
        version=row["version"],
        current_height=row["current_height"],
        target_height=row["target_height"],
        p2p_height=row["p2p_height"],
        explorer_height=row["explorer_height"],
        height_divergence=row["height_divergence"],
        tolerance=row["tolerance"],
        is_synchronized=_ub(row["is_synchronized"]),
        sync_reason=row["sync_reason"],
        peer_count=row["peer_count"],
        min_peers=row["min_peers"],
        active_connections=row["active_connections"],
        inbound_peers=row["inbound_peers"],
        outbound_peers=row["outbound_peers"],
        stale_peers=row["stale_peers"],
        peer_addresses=json.loads(row["peer_addresses_json"] or "[]"),
        congestion_level=row["congestion_level"],
        pending_swaps=row["pending_swaps"],
        cached_transactions=row["cached_transactions"],
        can_mine=_ub(row["can_mine"]),
        can_serve_blocks=_ub(row["can_serve_blocks"]),
        can_create_tx=_ub(row["can_create_tx"]),
        clock_skew_s=row["clock_skew_s"],
        error=row["error"],
    )


def insert_sample(conn: sqlite3.Connection, sample: Sample) -> int:
    cur = conn.execute(
        """
        INSERT INTO samples (
            ts, node, ip, ok, latency_ms, state, version,
            current_height, target_height, p2p_height, explorer_height,
            height_divergence, tolerance, is_synchronized, sync_reason,
            peer_count, min_peers, active_connections, inbound_peers,
            outbound_peers, stale_peers, peer_addresses_json,
            congestion_level, pending_swaps, cached_transactions,
            can_mine, can_serve_blocks, can_create_tx, clock_skew_s, error
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            sample.ts,
            sample.node,
            sample.ip,
            1 if sample.ok else 0,
            sample.latency_ms,
            sample.state,
            sample.version,
            sample.current_height,
            sample.target_height,
            sample.p2p_height,
            sample.explorer_height,
            sample.height_divergence,
            sample.tolerance,
            _b(sample.is_synchronized),
            sample.sync_reason,
            sample.peer_count,
            sample.min_peers,
            sample.active_connections,
            sample.inbound_peers,
            sample.outbound_peers,
            sample.stale_peers,
            json.dumps(sample.peer_addresses),
            sample.congestion_level,
            sample.pending_swaps,
            sample.cached_transactions,
            _b(sample.can_mine),
            _b(sample.can_serve_blocks),
            _b(sample.can_create_tx),
            sample.clock_skew_s,
            sample.error,
        ),
    )
    commit(conn)
    return int(cur.lastrowid)


def samples_since(
    conn: sqlite3.Connection,
    since: float,
    until: float | None = None,
) -> list[Sample]:
    query = "SELECT * FROM samples WHERE ts >= ?"
    args: list[Any] = [since]
    if until is not None:
        query += " AND ts <= ?"
        args.append(until)
    query += " ORDER BY ts ASC"
    return [_row_to_sample(row) for row in conn.execute(query, args).fetchall()]


def get_last_sample(conn: sqlite3.Connection, node: str) -> Sample | None:
    row = conn.execute(
        "SELECT * FROM samples WHERE node = ? ORDER BY ts DESC LIMIT 1", (node,)
    ).fetchone()
    return _row_to_sample(row) if row else None


def get_node_state(conn: sqlite3.Connection, node: str) -> NodeState:
    row = conn.execute("SELECT * FROM node_state WHERE node = ?", (node,)).fetchone()
    if row is None:
        return NodeState(node=node)
    return NodeState(
        node=row["node"],
        status=row["status"],
        consecutive_failures=row["consecutive_failures"],
        last_ok_ts=row["last_ok_ts"],
        last_check_ts=row["last_check_ts"],
        last_alert_ts=row["last_alert_ts"],
        open_incident_id=row["open_incident_id"],
        last_peer_count=row["last_peer_count"],
        first_seen_version=row["first_seen_version"],
    )


def upsert_node_state(conn: sqlite3.Connection, state: NodeState) -> None:
    conn.execute(
        """
        INSERT INTO node_state (
            node, status, consecutive_failures, last_ok_ts, last_check_ts,
            last_alert_ts, open_incident_id, last_peer_count, first_seen_version
        ) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(node) DO UPDATE SET
            status = excluded.status,
            consecutive_failures = excluded.consecutive_failures,
            last_ok_ts = excluded.last_ok_ts,
            last_check_ts = excluded.last_check_ts,
            last_alert_ts = excluded.last_alert_ts,
            open_incident_id = excluded.open_incident_id,
            last_peer_count = excluded.last_peer_count,
            first_seen_version = excluded.first_seen_version
        """,
        (
            state.node,
            state.status,
            state.consecutive_failures,
            state.last_ok_ts,
            state.last_check_ts,
            state.last_alert_ts,
            state.open_incident_id,
            state.last_peer_count,
            state.first_seen_version,
        ),
    )
    commit(conn)


def open_incident(
    conn: sqlite3.Connection,
    node: str,
    kind: str,
    severity: str,
    detail: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO incidents (node, kind, severity, opened_ts, detail) VALUES (?,?,?,?,?)",
        (node, kind, severity, time.time(), detail),
    )
    commit(conn)
    return int(cur.lastrowid)


def resolve_incident(conn: sqlite3.Connection, node: str) -> None:
    conn.execute(
        "UPDATE incidents SET resolved_ts = ? WHERE node = ? AND resolved_ts IS NULL",
        (time.time(), node),
    )
    commit(conn)


def get_report(conn: sqlite3.Connection, date: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM daily_reports WHERE date = ? ORDER BY sent_ts DESC LIMIT 1",
        (date,),
    ).fetchone()
    return dict(row) if row else None


def get_latest_report(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM daily_reports ORDER BY sent_ts DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def mark_report_sent(
    conn: sqlite3.Connection,
    date: str,
    summary: str,
    kind: str = "scheduled",
) -> None:
    conn.execute(
        """
        INSERT INTO daily_reports (date, kind, sent_ts, summary) VALUES (?,?,?,?)
        ON CONFLICT(date, kind) DO UPDATE SET
            sent_ts = excluded.sent_ts,
            summary = excluded.summary
        """,
        (date, kind, time.time(), summary),
    )
    commit(conn)


def was_report_sent(conn: sqlite3.Connection, date: str) -> bool:
    """True if any report (manual or scheduled) exists for this date."""
    row = conn.execute("SELECT 1 FROM daily_reports WHERE date = ?", (date,)).fetchone()
    return row is not None


def scheduled_report_sent(conn: sqlite3.Connection, date: str) -> bool:
    """True if the automatic daily summary was sent for this date.

    Manual `/report` runs are stored with kind='manual' and never block the
    scheduled 09:00 summary.
    """
    row = conn.execute(
        "SELECT 1 FROM daily_reports WHERE date = ? AND kind = 'scheduled'",
        (date,),
    ).fetchone()
    return row is not None


def prune_old_data(conn: sqlite3.Connection, retention_days: int = 7) -> dict[str, int]:
    """Delete samples, incidents, and reports older than the retention window."""
    cutoff = time.time() - retention_days * 86400
    samples = conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,)).rowcount
    incidents = conn.execute(
        "DELETE FROM incidents WHERE opened_ts < ?", (cutoff,)
    ).rowcount
    reports = conn.execute(
        "DELETE FROM daily_reports WHERE sent_ts < ?", (cutoff,)
    ).rowcount
    commit(conn)
    return {"samples": samples, "incidents": incidents, "reports": reports}


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta (key, value) VALUES (?,?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    commit(conn)


def build_rollup(
    conn: sqlite3.Connection,
    since: float,
    until: float,
    nodes: list[NodeConfig],
) -> dict[str, Any]:
    """Aggregate samples into a compact, LLM-friendly summary."""
    samples = samples_since(conn, since, until)
    by_node: dict[str, list[Sample]] = {}
    for sample in samples:
        by_node.setdefault(sample.node, []).append(sample)

    versions: dict[str, list[str]] = {}
    notes: list[str] = []
    nodes_out: list[dict[str, Any]] = []
    total_incidents = 0

    for node in nodes:
        rows = by_node.get(node.name, [])
        if not rows:
            nodes_out.append({"node": node.name, "ip": node.ip, "samples": 0, "last_state": "no-data"})
            notes.append(f"{node.name}: no samples in window")
            continue

        oks = [r for r in rows if r.ok]
        uptime = 100.0 * len(oks) / len(rows) if rows else 0.0
        lats = sorted(r.latency_ms for r in oks if r.latency_ms is not None)
        peers = [r.peer_count for r in oks if r.peer_count is not None]
        sync_vals = [r.is_synchronized for r in oks if r.is_synchronized is not None]
        heights = [r.current_height for r in oks if r.current_height is not None]
        skews = [abs(r.clock_skew_s) for r in oks if r.clock_skew_s is not None]
        congestion = sorted({r.congestion_level for r in oks if r.congestion_level})
        stale_max = max((r.stale_peers or 0) for r in oks) if oks else None

        for version in {r.version for r in oks if r.version}:
            versions.setdefault(version, []).append(node.name)

        incidents = conn.execute(
            "SELECT COUNT(*) AS c FROM incidents WHERE node = ? AND opened_ts >= ? AND opened_ts <= ?",
            (node.name, since, until),
        ).fetchone()["c"]
        total_incidents += incidents

        last = rows[-1]
        nodes_out.append(
            {
                "node": node.name,
                "ip": node.ip,
                "samples": len(rows),
                "uptime_pct": round(uptime, 1),
                "lat_avg_ms": round(sum(lats) / len(lats), 1) if lats else None,
                "lat_p95_ms": round(_percentile(lats, 95), 1) if lats else None,
                "peers_min": min(peers) if peers else None,
                "peers_max": max(peers) if peers else None,
                "peers_avg": round(sum(peers) / len(peers), 1) if peers else None,
                "peer_trend": (peers[-1] - peers[0]) if len(peers) >= 2 else 0,
                "sync_uptime_pct": round(100.0 * sum(1 for v in sync_vals if v) / len(sync_vals), 1)
                if sync_vals
                else None,
                "incidents": incidents,
                "height_start": heights[0] if heights else None,
                "height_end": heights[-1] if heights else None,
                "version": last.version if last.ok else None,
                "clock_skew_max_s": round(max(skews), 1) if skews else None,
                "congestion": congestion,
                "stale_peers_max": stale_max,
                "last_state": last.state if last.ok else "unreachable",
            }
        )

    latest = {name: by_node[name][-1] for name in by_node if by_node[name]}
    seed_ips = [node.seed_ip for node in nodes if node.seed_ip]
    missing_pairs: set[tuple[str, str]] = set()
    for node in nodes:
        if not node.seed_ip:
            continue
        sample = latest.get(node.name)
        if not sample or not sample.ok:
            continue
        seen = set(sample.peer_addresses)
        for other_ip in seed_ips:
            if other_ip != node.seed_ip and other_ip not in seen:
                missing_pairs.add(tuple(sorted((node.seed_ip, other_ip))))
    for left, right in sorted(missing_pairs):
        notes.append(f"seed peering gap: {left} <-> {right}")

    if len(versions) > 1:
        rendered = ", ".join(f"{v} ({len(names)})" for v, names in sorted(versions.items()))
        notes.append(f"version drift: {rendered}")

    down = [n["node"] for n in nodes_out if n.get("last_state") == "unreachable"]
    if down:
        notes.append("unreachable in window: " + ", ".join(down))

    return {
        "window": {"start": _iso(since), "end": _iso(until)},
        "fleet": {
            "nodes_total": len(nodes),
            "nodes_currently_up": sum(
                1 for n in nodes_out if n.get("last_state") not in (None, "unreachable", "no-data")
            ),
            "versions": versions,
            "incidents": total_incidents,
            "notes": notes,
        },
        "nodes": nodes_out,
    }