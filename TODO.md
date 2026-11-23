# HumidityDome — Build To-Do

> Infrastructure heartbeat for the 1stBase blockchain. Polls the 5 seed nodes every
> 30 minutes, validates sync / peer / version state, emails a daily summary at
> **07:00 UTC+2**, and sends instant alerts when a node faults. Runs on a Fly.io
> Sprite that sleeps between ticks so the bill stays at **$0**.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done

---

## 0. Repo & secrets
- [x] Remote is `https://github.com/1stBaseFoundation/HumidityDome.git`
- [x] `git init` + first commit + push to `origin/main`
- [x] `.gitignore` covers `.env`, `*.db*`, `__pycache__/`, `.venv/`
- [ ] GitHub Actions secrets: `SPRITE_URL`, `SPRITES_TOKEN`
- [ ] Sprites connectors created + granted to the `humiditydome` Sprite:
  - [ ] Groq (custom API)
  - [ ] Resend (custom API)

## 1. Scaffold
- [x] `requirements.txt` — fastapi, uvicorn, httpx, pyyaml, pytest, pytest-asyncio
- [x] `config/nodes.yaml` — 5 seeds, thresholds, emails, Groq model, connector ids
- [x] `src/humiditydome/__init__.py`
- [x] `tests/` skeleton + `conftest.py`

## 2. Config
- [x] `config.py` — load + validate YAML, env overrides (`HUMIDITYDOME_DB`, `HUMIDITYDOME_DRYRUN`)
- [x] Fields: poll interval/timeout, report time + UTC offset, alert thresholds,
      Groq model + connector gateway URL, Resend gateway URL, nodes[]

## 3. Probe
- [x] `models.py` — `Sample`, `NodeState`, `Alert`, `NodeConfig`
- [x] `probe.py` — `fetch_status()` GET `/api/node/state/status`, latency
- [x] Derived: height divergence, tolerance, clock skew, stale peers, peer addresses,
      congestion, capabilities
- [x] `tests/test_probe.py` using the real JSON shape

## 4. Store (SQLite, WAL)
- [x] `store.py` — schema + init (WAL, durable on Sprite disk)
- [x] Tables: `samples`, `node_state`, `incidents`, `daily_reports`, `meta`
- [x] `insert_sample`, `get_last_sample`, `get_node_state`, `upsert_node_state`
- [x] `open_incident`, `resolve_incident`
- [x] `samples_since`, `build_rollup` (compact, LLM-ready)
- [x] `mark_report_sent`, `was_report_sent`, `get_report`, `get_latest_report`
- [x] `get_meta`, `set_meta`
- [x] DB path: `/home/sprite/humiditydome/humiditydome.db` (persistent, **not** `/tmp`)

## 5. Alerts
- [x] `alerts.py` — `evaluate()` + `process_sample()` state machine
- [x] Rules: unreachable, peer drop (≥40% or `< minPeers`), sync divergence
      (`> tolerance`), clock skew, height divergence, version change
- [x] Debounce (N consecutive failures) + per-node cooldown
- [x] Recovery notices (`send_recovery`)
- [x] `fleet_alerts()` — isolated-from-all-seeds detection + recovery
- [x] `tests/test_alerts.py` — transitions, debounce, peer drop, recovery, cooldown

## 6. LLM (Groq via connector)
- [x] `llm.py` — `summarize()` posts to the Sprites gateway
- [x] Strict prompt: "use only the provided figures; do not invent values"
- [x] Dry-run fallback summary
- [x] Default model `qwen/qwen3.8-27b` (config-overridable)

## 7. Mail (Resend via connector)
- [x] `mail.py` — `send_email()` posts to the Sprites gateway
- [x] From `noreply@1stbase.org`, Reply-To `info@1stbase.org`
- [x] Alert email template + daily summary HTML template
- [x] Dry-run no-op

## 8. Report
- [x] `report.py` — `build_and_send_report()`
- [x] Rollup last 24 h → compact JSON → Groq → HTML → Resend
- [x] Deterministic header table + LLM prose
- [x] `mark_report_sent` / `is_report_time` (fixed UTC+2)

## 9. API
- [x] `app.py` FastAPI
- [x] `GET /healthz`
- [x] `POST /tick` — fetch all nodes → store → alerts → maybe report → summary JSON
- [x] `GET /status` — last known state
- [x] `GET /report?date=YYYY-MM-DD` (latest if omitted)
- [x] `POST /report` — force a report
- [x] Idempotency guard (skip a tick that runs too soon after the previous one)

## 10. Sprites deploy
- [x] `sprite create humiditydome` (org: `1stbase`)
- [x] Install deps into `/home/sprite/humiditydome/.venv`
- [x] `sprite-env services create web --cmd .../uvicorn --args "...--port,8080" --http-port 8080`
- [x] Verify `curl localhost:8080/healthz` via `sprite exec`
- [x] Live `/tick` from the Sprite — all 5 seeds reachable
- [ ] Verify cold-wake: hit the URL, confirm the SQLite DB persisted
- [x] Remove `HUMIDITYDOME_DRYRUN=1` once connectors are configured

## Deployed
- Sprite: `humiditydome` (org `1stbase`)
- ID: `sprite-94148461-73f7-4749-a9c0-daada56a3767`
- URL: `https://humiditydome-b3tcz.sprites.app`
- Auth: `sprite` (token-only)
- Service: `web` (uvicorn on :8080, production mode)
- Nodes: 6 (5 seeds + `node-1stbase`)
- DB: `/home/sprite/humiditydome/humiditydome.db`

## 11. Schedulers & connectors
- [x] Groq: key + custom connector, gateway URL in config
- [x] Resend: key + connector, gateway URL in config
- [x] Resend: verify `1stbase.org` DNS (SPF/DKIM/MX/DMARC)
- [x] GitHub Actions `tick.yml` — cron `*/30 * * * *`
- [x] Add `SPRITE_URL`, `SPRITES_TOKEN` secrets
- [ ] Manual `workflow_dispatch` test

## 12. Tests & QA
- [x] `ruff check src tests` — clean
- [x] `pytest` — 52 passed
- [x] Coverage — 96% line / branch; every business file ≥ 90%
- [x] Local dry-run against the 5 live seeds — all reachable, report generated
- [x] Sprite dry-run `/tick` — all 5 seeds reachable, status persisted
- [x] Groq connector live test — `qwen/qwen3.8-27b` replied OK
- [x] Resend connector live test — test email accepted (to `contact@1stbase.org`)
- [ ] Force a real fault → real alert email
- [x] Force daily report → real email (production to `info@1stbase.org`)
- [x] Add `node-1stbase` (HTTPS) + markdown→HTML email formatting
- [ ] Verify SQLite survives a cold wake

## 13. Demo
- [ ] Screenshot raw JSON next to the clean email
- [ ] Version-drift callout (1.4.4 – 1.4.8 across the fleet)
- [ ] Seed-peering-gap callout (`8.222.204.112` ↔ `84.12.114.48`)
- [ ] Architecture + cost slide ($0)

---

## Decisions (locked)
- [x] From: `noreply@1stbase.org`, Reply-To: `info@1stbase.org`
- [x] Recovery emails: on
- [x] LLM: `qwen/qwen3.8-27b`
- [x] Keys: Sprites connectors (never in GitHub)

## Keys still to set up
- [ ] GitHub secrets: `SPRITE_URL`, `SPRITES_TOKEN`
- [ ] Groq API key (free tier) → Sprites connector
- [ ] Resend API key + DNS access for `1stbase.org` → Sprites connector

## Notes
- Resend account email is `contact@1stbase.org`; the sandbox can only deliver there.
- Production from/to: `noreply@1stbase.org` → `info@1stbase.org` (needs domain verified).
- Sprite service is still in dry-run (`HUMIDITYDOME_DRYRUN=1`) until DNS is verified.