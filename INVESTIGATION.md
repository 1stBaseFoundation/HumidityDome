# Investigation — `tick` 502s and dirty-shutdown hardening

Status: **partially resolved** (morning-miss root cause found: GitHub dropped the cron; Sprite 502s still under investigation)
Last updated: 2026-11-26

## Resolution so far

Two distinct problems were separated:

1. **Morning-summary miss (root cause found — CI/CD).** GitHub Actions was
   silently dropping the `*/30` schedule on this low-activity repo: runs were
   ~8–9h apart (03:15, 20:45, 11:50, 02:50, …), so the 07:00 UTC report tick
   never fired. Fixed by moving to hourly (`0 * * * *`) across the board and
   documenting an external 1-hour cron-job.org trigger as the reliable primary.
2. **Sprite 502s (still open — app/infra).** The 03:15 UTC run returned three
   502s ~52s apart. The retry logic worked correctly; the Sprite itself never
   answered. Cause is a **wake/connection artifact**, not a shutdown-handling gap
   (see the corrected lifecycle model below). Under investigation via Sprite logs.

The hardening from Steps 1–2 (observability + atomicity + retry) is landed and
green; it is **preventive**, not a fix for an active bug.

## Symptom

The `tick` GitHub Actions workflow intermittently fails. The most recent
scheduled run failed with a `502` after ~47s, while healthy ticks complete in
9–11s.

| Run | Time (UTC) | Duration | Result |
|---|---|---|---|
| 45288074430 | 2026-11-23 23:21 | 9s | 401 unauthorized |
| 45288760273 | 2026-11-23 23:25 | 8s | 401 unauthorized |
| 45289593748 | 2026-11-23 23:31 | 9s | success |
| 45344572953 | 2026-11-24 06:59 | 11s | success |
| 45462118229 | 2026-11-24 18:40 | 54s | **502** (curl ran ~47s) |

The 401s are a known, already-documented token problem (see README). The 502 is
a **different** failure mode and is what this document is about.

## What a 502 means here

The Sprite proxy returns `502` when the upstream app (uvicorn) does not answer
in time. That happens for at least two distinct reasons that look identical from
GitHub's side:

1. **Slow cold wake** — the Sprite was cold and took too long to boot; the
   proxy gave up before uvicorn was serving (leading hypothesis).
2. **Warm-suspend mid-request** — the VM paused while handling the request, so
   the response was never produced (less likely; see lifecycle below).

We cannot yet distinguish these from the workflow log alone. **Confirming which
one it is is the first goal of this work** — hence the logging emphasis below.

## Sprite lifecycle — what "idle" actually means (corrected)

Earlier notes claimed there was "no clean shutdown." That was imprecise. Fly.io
documents the lifecycle explicitly (docs.fly.io/sprites/concepts/lifecycle.md),
and **"idle" is activity-based, not CPU-usage-based**:

- A Sprite is **active** while anything is using it: a running command, a session
  producing output, **an open TCP connection to its URL, or a service handling
  traffic**.
- After ~**30 s** of no activity it pauses, in two stages:
  - **Warm** — VM **suspended**, memory frozen in place. Processes "pick up
    mid-thought, exactly where they were." Wake 100–500 ms. **Not** a process
    kill; no SIGTERM drain.
  - **Cold** — VM **fully stopped**, in-memory state dropped. Wake 1–2 s;
    processes restarted fresh by the runtime-owned Service.
- You don't choose warm vs cold and can't see the transition — warm first, cold
  on its own later.
- **Open TCP connections do not survive either pause** — clients reconnect on the
  next request. ← This is the likely 502 mechanism.
- **Disk persists** (real ext4, NVMe hot cache + *continuous* sync to object
  storage, not a hibernation snapshot). Memory does not. SQLite "works in every
  mode" on it, so WAL is safe across both pause types.

### Why a `lifespan` shutdown hook is NOT the fix

A graceful `lifespan` shutdown (checkpoint + `conn.close()`) only runs on an
actual process stop. A **warm** suspend freezes uvicorn without delivering a
drain, and a **cold** stop drops the process outright. So relying on graceful
shutdown is wrong for this platform — which is exactly why the **WAL-atomic
single-transaction tick** (Step 2) is the correct durability posture. The open
`conn` being abandoned on pause is harmless: WAL replays on next open.

### The 1–2 s cold-wake claim is optimistic

The docs' "1–2 s cold wake" is a best case, not a guarantee. A cold start must
page the ext4 back from object storage, re-exec uvicorn, import the app, run
`store.connect` (schema + migrate + WAL replay), and only then serve. Under a
cold cache or a large WAL that can exceed the proxy's patience → 502. This is
the leading hypothesis for the 03:15 failure and is explicitly on the list to
confirm from Sprite logs (below).

## Why a dirty shutdown WOULD have been silent (historical — now fixed)

Before Step 2, `run_tick` in `src/humiditydome/app.py` did, in order:

1. debounce check against `last_tick_ts`
2. **write `last_tick_ts` before any work**  ← the bug
3. `prune_old_data`
4. probe all nodes
5. per-node `insert_sample` — **each committed independently**
6. alerts + optional report

There was **no `try/finally`, no integrity check, no single-flight guard**, and
the SQLite connection was never closed. A stop mid-tick would therefore:

- leave `last_tick_ts` already advanced, so the next tick within the debounce
  window returned `"tick too soon"` and was **silently skipped** → a data gap;
- leave a **partial tick** (some nodes persisted, others missing) because each
  insert committed separately;
- never reconcile either condition.

Durability note: `journal_mode=WAL` + `synchronous=NORMAL` protects against
database *corruption* (a torn WAL tail rolls back on next open) but does **not**
make a multi-statement tick atomic, nor advance/rewind `last_tick_ts`. Step 2
fixed all of the above; this section is retained to explain the original risk.

> SQLite has no MANIFEST or LOCK file (that is LevelDB/RocksDB). The "manifest"
> role is played internally by the DB header + WAL. So the hardening does **not**
> add manifest/lock files — it relies on WAL atomicity plus a single transaction
> per tick.

## Working hypotheses (unconfirmed)

- **H1 — cold-wake vs proxy timeout (leading).** The Sprite was cold; the wake
  (ext4 page-in from object storage + uvicorn re-exec + app import + `store.connect`
  + WAL replay) exceeded the proxy's patience → 502. No tick ran; DB untouched.
  *Expected evidence:* `last_tick_ts` still at the previous successful tick; **no**
  `startup` log line for that run (uvicorn never finished booting); Sprite logs
  show a cold wake around 03:15.
- **H2 — warm-suspend mid-request.** Less likely (a service handling traffic
  counts as activity, so it shouldn't pause mid-request), but possible if the
  request raced the 30 s idle window. *Expected evidence:* a `startup` line and a
  partial phase sequence, then nothing; `last_tick_ts` not advanced (Step 2 rolls
  it back).
- **H3 — something else** (proxy/network blip, OOM, cold-cache object-storage
  latency spike). *Expected evidence:* neither of the above cleanly fits.

## Step 3 — confirm from Sprite logs (cold-wake vs proxy timeout)

`flyctl`/`sprite` are not installed on the dev box; run these where the CLI is
available, for the 03:15 UTC window:

```bash
flyctl logs -a <sprite-name>
# or: Sprites dashboard → <sprite> → Logs, around 03:15 UTC
```

Read the logs to decide H1 vs H2:

- **No `startup` line at all** → uvicorn never finished cold-booting → **H1**
  (cold-wake/proxy-timeout). The fix is a longer proxy patience or a keepalive,
  not app code.
- **`startup` line, then a partial phase sequence, no `done`** → **H2**
  (warm-suspend mid-request); Step 2's rollback already protects data.
- Either way, confirm `last_tick_ts` in `/status` matches the last *successful*
  tick (Step 2 guarantees this).

If H1 is confirmed, the durable fix is an external 1-hour cron-job.org trigger
(already the recommended primary) plus, optionally, a Sprites **keepalive/Tasks
hold** around the request so the VM is warm before the tick lands.

## Plan: observability first, then logical hardening

Because we are still speculating, **step 1 is to make the system observable** so
the next 502 is diagnosable rather than another guess. Only then do we apply the
targeted fixes. No manifest/lock files, no external cache — keep it minimal.

### Step 1 — debug logging (do first)

Add the stdlib `logging` module (none exists today) and instrument the tick
path so a run can be traced end-to-end. Target log points:

- `run_tick` entry/exit with `run_id`, `now`, and the debounce decision
  (skipped vs. running, with `last_tick_ts`).
- Each phase boundary: `prune` → `probe_start` → `probe_done` (per-node ok/fail +
  latency) → `insert` (per node) → `alerts` → `report`.
- The `last_tick_ts` write, with old → new value.
- A completion line with total duration and node count.
- `probe.fetch_status` failures (already captured as `Sample.error`; surface at
  debug/warning).
- Startup: config path, DB path, node count, `dry_run`, and a `quick_check`
  result (see step 2).

Format: structured key=value lines (e.g. `tick run_id=… phase=probe_done
nodes=6 dur_ms=…`) so a suspended run is obvious from the *last* line emitted.
Level configurable via `HUMIDITYDOME_LOG_LEVEL` (default `INFO`).

### Step 2 — logical hardening (after logging lands)

- **Atomic tick.** Make the write phase of `run_tick` a single transaction
  (`BEGIN … COMMIT`) covering samples, node state, incidents, and the
  `last_tick_ts` update. A dirty stop then rolls the whole tick back via WAL —
  no partial entries, and `last_tick_ts` does not advance.
- **`last_tick_ts` written last, inside that transaction.** An interrupted tick
  re-runs on the next trigger instead of being skipped.
- **Single-flight guard.** An in-process `asyncio.Lock` so overlapping `/tick`
  calls return `{"ok": true, "skipped": true, "reason": "run in progress"}`
  (HTTP 200). In-memory only — a restart clears it; nothing to hang.
- **Integrity smoke test.** `PRAGMA quick_check` at startup and in `/healthz`;
  `PRAGMA wal_checkpoint(TRUNCATE)` once on startup. Cheap insurance, not a
  per-tick gate.
- **Workflow retry.** `curl --retry 2 --retry-delay 5 --retry-all-errors
  --max-time 90` in `tick.yml` so a transient wake-up 502 retries once instead
  of failing the run.

### Step 4 — README screenshot (done)

The resolved-alert screenshot is already added to the README
(`docs/screenshot-resolved-alert.png`). Note: the image could not be visually
verified during this analysis — the model used does not support image input — so
confirm it depicts the resolved/recovery alert.

## Files changed (landed)

- `src/humiditydome/logging_setup.py` (new) — logger config from env.
- `src/humiditydome/app.py` — instrument `run_tick`; single-flight; atomic
  transaction; integrity in `/healthz`; hourly docstring.
- `src/humiditydome/store.py` — `check_integrity`; transaction helper;
  `last_tick_ts` only on success.
- `src/humiditydome/probe.py` — debug log on failure.
- `tests/` — single-flight skip, rollback-on-failure (timestamp not advanced),
  integrity ok/bad.
- `.github/workflows/tick.yml` — retry/back-off; cron `0 * * * *` (hourly).
- `config/nodes.yaml` + `config.py` — `interval_minutes: 60`.
- `README.md` / `TODO.md` — hourly polling, recovery model, resolved screenshot.

## Open questions

- Is the Sprite a single uvicorn worker? (Single-flight assumes yes; if multiple
  workers, the guard must be per-process and the DB transaction still holds.)
- Do we want a `warning`-severity alert email when a tick is skipped due to
  single-flight, or only a log line? (Current lean: log only.)
- If H1 (cold-wake) is confirmed, do we add a Sprites keepalive/Tasks hold around
  the request, or rely on the external 1-hour cron-job.org trigger alone?