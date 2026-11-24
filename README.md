# HumidityDome

**Infrastructure heartbeat for the 1stBase blockchain.**

HumidityDome is a small, self-hosted monitoring agent that watches the health of your
blockchain seed nodes. It polls each node, validates its sync / peer / version state, and
emails a clean daily summary plus instant alerts when something breaks.

> **Status: WIP** — v1 targets the 5 seed nodes and preview node. Websites are out of
> scope for now.

---

## Why

Running a blockchain means babysitting seed nodes. HumidityDome does the babysitting: it
turns noisy node JSON into a one-line-per-node morning email, and pings you the moment a
node goes dark, loses peers, or stops syncing.

## How it works

```
            every 30 min (GitHub Actions cron, free)
                          |
                          v
              +---------------------+
              |  Fly.io Sprite      |   sleeps when idle -> $0
              |  (FastAPI service)  |
              +----------+----------+
                         |
        +----------------+----------------+
        |                |                |
        v                v                v
   seed nodes        Groq (LLM)      Resend (email)
   (5x HTTP)         (summary)       (alerts + daily)
        |
        v
   SQLite (persistent disk)
```

- The Sprite **sleeps when idle** (no compute cost) and **wakes on each tick**.
- State lives in **SQLite on the Sprite's persistent disk**, so it survives every sleep.
- The LLM only writes prose; **all numbers are computed in code** — that is what keeps the
  summary "error-free".

## Monitored signals (v1)

- Reachability / HTTP latency
- `state` (`ready`, …) and `version`
- Sync: `isSynchronized` + divergence between `currentHeight` / `targetHeight` /
  `p2pHeight` / `explorerHeight` vs `tolerance`
- Peers: count vs `minPeers`, inbound/outbound, **stale peers**, **seed-to-seed
  connectivity matrix**
- Clock skew (node `timestamp` vs UTC)
- Mempool: `congestionLevel`, `pendingSwaps`, `cachedTransactions`
- Capabilities: `canMine`, `canServeBlocks`, `canCreateTx`, `mining.hasEnoughPeers`

## Seed nodes (v1)

| Name     | Endpoint                                                |
|----------|---------------------------------------------------------|
| seed-150 | `http://150.136.172.164:3001/api/node/state/status`     |
| seed-129 | `http://129.153.171.162:3001/api/node/state/status`     |
| seed-145 | `http://145.241.103.198:3001/api/node/state/status`     |
| seed-8   | `http://8.222.204.112:3001/api/node/state/status`       |
| seed-84  | `http://84.12.114.48:3001/api/node/state/status`        |

## Alerts

A node triggers an email alert when it:

- is **unreachable** (connection error / timeout / non-2xx / `success:false`)
- **loses peers** (≥40% drop vs last check, or below `minPeers`)
- has **unstable sync** (`isSynchronized:false`, `state != ready`, or height divergence
  beyond `tolerance`)
- has **clock skew** beyond the threshold
- shows a **seed-to-seed peering gap**

Alerts are debounced (default 2 consecutive failures) and rate-limited (per-node cooldown)
to avoid spam.

## Daily report

At **07:00 UTC+2** the agent compiles the last 24 h into a compact rollup, asks
`qwen/qwen3.8-27b` (Groq free tier) to write a professional summary, and emails it to
`info@1stbase.org` via Resend.

## Tech stack

- Python 3.12, FastAPI, httpx, SQLite (WAL)
- Fly.io **Sprites** (sleep-when-idle VM)
- GitHub Actions (cron trigger)
- Groq (LLM) + Resend (email) via **Sprites connectors**

## Repo layout

```
.
├── README.md
├── TODO.md
├── requirements.txt
├── .github/workflows/tick.yml
├── config/nodes.yaml
├── src/humiditydome/
│   ├── app.py        # FastAPI: /healthz /tick /status /report
│   ├── config.py
│   ├── models.py
│   ├── probe.py      # fetch + parse node status
│   ├── store.py      # SQLite state
│   ├── alerts.py     # fault detection
│   ├── llm.py        # Groq via connector
│   ├── mail.py       # Resend via connector
│   └── report.py     # daily rollup + email
└── tests/
```

## Setup

### 1. Prerequisites
- A Fly.io account and the `sprite` CLI (`curl -fsSL https://sprites.dev/install.sh | sh`)
- A Groq API key (free tier)
- A Resend account + DNS access for `1stbase.org`
- The GitHub repo `1stBaseFoundation/HumidityDome`

### 2. Sprites connectors (keys never touch the code)
- **Groq**: custom API connector, `base_api_url=https://api.groq.com`, header `Bearer`,
  token = Groq key. Grant access to the `humiditydome` Sprite.
- **Resend**: custom API connector, `base_api_url=https://api.resend.com`, header `Bearer`,
  token = Resend key. Grant access to the `humiditydome` Sprite.

### 3. Resend
1. Add `1stbase.org` in Resend → **Domains**.
2. Add the SPF / DKIM / DMARC DNS records it gives you.
3. Create a **Sending access** API key.
4. From: `noreply@1stbase.org` (Reply-To `info@1stbase.org`), To: `info@1stbase.org`.
   You do **not** need to create a `noreply@` mailbox — Resend authenticates the domain,
   not the mailbox.

### 4. Deploy the Sprite
```bash
sprite create humiditydome
sprite use humiditydome

sprite exec -- bash -c "git clone https://github.com/1stBaseFoundation/HumidityDome /home/sprite/humiditydome && \
  pip install -r /home/sprite/humiditydome/requirements.txt"

sprite-env services create web \
  --cmd uvicorn \
  --args "humiditydome.app:app,--host,0.0.0.0,--port,8080" \
  --dir /home/sprite/humiditydome/src \
  --http-port 8080
```

### 5. GitHub Actions

Add two repository secrets (**Settings → Secrets and variables → Actions**):

- `SPRITE_URL` — the Sprite's URL from `sprite info`, e.g. `https://<sprite-name>-<org-id>.sprites.app`
- `SPRITES_TOKEN` — a **Sprites token**, *not* a Fly.io organization token

#### Which token goes in `SPRITES_TOKEN`?

This is the easiest step to get wrong. Fly.io has two different token screens:

| Where you create it | Token format | Works for Sprite URLs? |
|---|---|---|
| **Sprites → Tokens** (the Sprites dashboard) | `org-slug/org-id/token-id/token-value`, e.g. `1stbase/…` | ✅ Yes |
| **Organization → Tokens** (Fly.io org settings) | `FlyV1…` | ❌ No — returns `401 {"error":"unauthorized"}` |

Create the token under **Sprites → Tokens**, with the correct organization selected
(the org that owns the Sprite — `1stbase` in this deployment). Sign in as the
account that owns that org, then copy the **entire** token string.

#### Troubleshooting `401 unauthorized`

If the workflow fails with:

```
curl: (22) The requested URL returned error: 401
{"error":"unauthorized"}
```

the `SPRITE_URL` is reachable but `SPRITES_TOKEN` is not a valid Sprites token.
Almost always this means a `FlyV1…` Fly.io organization token was used instead of
a **Sprites → Tokens** token. Recreate the token in the Sprites dashboard and
update the secret.

The workflow posts to `/tick` every 30 minutes.

## Local development

```bash
pip install -r requirements.txt
uvicorn humiditydome.app:app --reload
curl -X POST localhost:8000/tick
```

Set `HUMIDITYDOME_DRYRUN=1` to skip real emails while developing.

## Cost

- **Sprites**: $0 while idle; a few seconds of compute per tick.
- **Groq**: free tier (1–2 calls/day, ~400-token prompts).
- **Resend**: free tier (3,000 emails/month).
- **GitHub Actions**: free for public repos.

**Target: $0/month.**

## Security

- No secrets in the repo. Groq / Resend tokens live in **Sprites connectors**;
  `SPRITES_TOKEN` lives in **GitHub Actions secrets**.
- `SPRITES_TOKEN` must be a **Sprites → Tokens** token (`org-slug/…`), not a
  Fly.io organization token (`FlyV1…`).
- The Sprite URL is private by default (org-only, token-authenticated).

## License

TBD
