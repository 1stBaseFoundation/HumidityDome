# HumidityDome — Build To-Do

> Infrastructure heartbeat for the 1stBase blockchain. Polls the 6 nodes (5 seeds +
> preview node) every 30 minutes, validates sync / peer / version state, emails a daily
> summary at **07:00 UTC+2**, and sends instant alerts when a node faults. Runs on a
> Fly.io Sprite that sleeps between ticks.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done

---

## 0. Repo & secrets
- [x] Remote is `https://github.com/1stBaseFoundation/HumidityDome.git`
- [x] `git init` + commits + push to `origin/main`
- [x] `.gitignore` covers `.env`, `*.db*`, `__pycache__/`, `.venv/`, `.sprite`
- [x] GitHub Actions secrets: `SPRITE_URL`, `SPRITES_TOKEN`
- [x] Sprites connectors created + granted to the `humiditydome` Sprite:
  - [x] Groq (custom API)
  - [x] Resend (custom API)
- [x] MIT license

## 1. Scaffold
- [x] `requirements.txt`
- [x] `config/nodes.yaml` — 6 nodes, thresholds, emails, Groq model, connector gateway URLs
- [x] `src/humiditydome/__init__.py`
- [x] `tests/` skeleton + `conftest.py`

## 2. Config
- [x] `config.py` — load + validate YAML, env overrides
- [x] Fields: poll interval/timeout, report time + UTC offset, alert thresholds,
      Groq model + gateway URL, Resend gateway URL, nodes[]

## 3. Probe
- [x] `models.py` — `Sample`, `NodeState`, `Alert`, `NodeConfig` (HTTP/HTTPS)
- [x] `probe.py` — `fetch_status()`, latency
- [x] Derived: height divergence, tolerance, clock skew, stale peers, peer addresses,
      congestion, capabilities
- [x] `tests/test_probe.py`

## 4. Store (SQLite, WAL)
- [x] `store.py` — schema + init
- [x] Tables: `samples`, `node_state`, `incidents`, `daily_reports`, `meta`
- [x] Sample / state / incident / report / meta functions
- [x] `build_rollup` (compact, LLM-ready)
- [x] DB path: `/home/sprite/humiditydome/humiditydome.db`

## 5. Alerts
- [x] `alerts.py` — `evaluate()` + `process_sample()` state machine
- [x] Rules: unreachable, peer drop, sync divergence, height divergence, clock skew,
      version change, isolated node
- [x] Debounce (2 consecutive failures)
- [x] **Repeat alerts at most once per 24 h per node** (no duplicate emails for the same
      ongoing issue; recovery notices still sent)
- [x] `fleet_alerts()` — isolated-from-all-seeds detection + recovery
- [x] `tests/test_alerts.py`

## 6. LLM (Groq via connector)
- [x] `llm.py` — `summarize()`
- [x] Strict prompt
- [x] Dry-run fallback
- [x] Default model `qwen/qwen3.8-27b`

## 7. Mail (Resend via connector)
- [x] `mail.py` — `send_email()`
- [x] From `noreply@1stbase.org`, Reply-To `info@1stbase.org`
- [x] Alert + daily HTML templates
- [x] Markdown → Outlook-friendly HTML

## 8. Report
- [x] `report.py` — `build_and_send_report()`
- [x] Rollup → Groq → HTML → Resend
- [x] Deterministic table + LLM prose
- [x] `mark_report_sent` / `is_report_time` (UTC+2)

## 9. API
- [x] `app.py` FastAPI
- [x] `GET /healthz`, `POST /tick`, `GET /status`, `GET /report`, `POST /report`
- [x] Idempotency guard
- [x] Verbose `[G3:tick]` logging removed

## 10. Sprites deploy
- [x] `sprite create humiditydome` (org: `1stbase`)
- [x] Install deps into `/home/sprite/humiditydome/.venv`
- [x] `sprite-env services create web ... --http-port 8080`
- [x] Verify `/healthz`
- [x] Live `/tick` — all 6 nodes reachable
- [x] Verify cold-wake: hit the URL, confirm the SQLite DB persisted
- [x] Remove `HUMIDITYDOME_DRYRUN=1` (production mode)

## Deployed
- Sprite: `humiditydome` (org `1stbase`)
- ID: `sprite-94148461-73f7-4749-a9c0-daada56a3767`
- URL: `https://humiditydome-b3tcz.sprites.app`
- Auth: `sprite` (token-only)
- Service: `web` (uvicorn on :8080, production mode)
- Nodes: 6 (5 seeds + `node-1stbase`)
- DB: `/home/sprite/humiditydome/humiditydome.db`

## 11. Schedulers & connectors
- [x] Groq connector
- [x] Resend connector
- [x] Resend DNS verified
- [x] GitHub Actions `tick.yml` — cron `*/30 * * * *`
- [x] `SPRITE_URL`, `SPRITES_TOKEN` secrets
- [x] Manual `workflow_dispatch` test (green)

## 12. Tests & QA
- [x] `ruff check src tests` — clean
- [x] `pytest` — 62 passed
- [x] Coverage — 97% line / branch; every business file ≥ 90%
- [x] Local dry-run against live seeds
- [x] Sprite dry-run `/tick`
- [x] Groq connector live test
- [x] Resend connector live test
- [x] Force a real fault → real alert email (seed-150 offline; alert received)
- [x] Force daily report → real email
- [x] Add `node-1stbase` (HTTPS) + markdown→HTML formatting
- [x] Verify SQLite survives a cold wake

## 13. Demo
- [x] Email summary screenshot (`docs/screenshot-email-summary.png`)
- [ ] Version-drift callout (1.4.4 – 1.4.8 across the fleet)
- [ ] Seed-peering-gap callout (`8.222.204.112` ↔ `84.12.114.48`)
- [ ] Architecture + cost slide

---

## Decisions (locked)
- [x] From: `noreply@1stbase.org`, Reply-To: `info@1stbase.org`
- [x] Recovery emails: on
- [x] LLM: `qwen/qwen3.8-27b`
- [x] Keys: Sprites connectors (never in GitHub)
- [x] Repeat alerts: at most once per 24 h per node

## Notes
- Resend account email is `contact@1stbase.org`; production sends from
  `noreply@1stbase.org` to `info@1stbase.org`.
- Sprite service runs in production mode (not dry-run).