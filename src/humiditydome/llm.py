"""Groq LLM summary via the Sprites connector gateway."""
from __future__ import annotations

import json
from typing import Any

import httpx

from .config import Config

SYSTEM_PROMPT = (
    "You are HumidityDome, a precise infrastructure reporter for a blockchain "
    "operations team. You receive a compact JSON health rollup. Write a concise, "
    "professional summary for a technical operator. "
    "Rules: use ONLY the numbers and facts in the JSON; never invent values; "
    "call out outages, version drift, peer problems, sync problems, clock skew, "
    "and seed peering gaps; keep it under 220 words; use short sections with "
    "plain sentences. Do not include raw JSON."
)


async def summarize(rollup: dict[str, Any], cfg: Config) -> str:
    """Ask the configured model to turn the rollup into prose."""
    if cfg.dry_run:
        return fallback_summary(rollup)

    url = f"{cfg.llm.gateway_base_url.rstrip('/')}/openai/v1/chat/completions"
    body = {
        "model": cfg.llm.model,
        "temperature": cfg.llm.temperature,
        "max_tokens": cfg.llm.max_tokens,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(rollup, separators=(",", ":"))},
        ],
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, json=body)
        response.raise_for_status()
        data = response.json()

    return data["choices"][0]["message"]["content"].strip()


def fallback_summary(rollup: dict[str, Any]) -> str:
    """Deterministic summary used for local dry runs (no LLM call)."""
    fleet = rollup.get("fleet", {})
    lines = [
        "HUMIDITYDOME DAILY REPORT (dry run)",
        (
            f"Nodes: {fleet.get('nodes_currently_up')}/{fleet.get('nodes_total')} up. "
            f"Incidents: {fleet.get('incidents', 0)}."
        ),
    ]
    for node in rollup.get("nodes", []):
        lines.append(
            f"- {node['node']} ({node.get('ip')}): {node.get('last_state')} | "
            f"uptime {node.get('uptime_pct')}% | peers avg {node.get('peers_avg')} | "
            f"version {node.get('version')} | latency {node.get('lat_avg_ms')} ms"
        )
    for note in fleet.get("notes", []):
        lines.append(f"Note: {note}")
    return "\n".join(lines)