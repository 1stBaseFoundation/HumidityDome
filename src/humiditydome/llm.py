"""Groq LLM summary via the Sprites connector gateway.

The model is required to return a strict JSON envelope which we validate before
accepting. Anything that does not parse or match the schema is rejected with an
``LLMError`` so the caller can fall back to the deterministic summary — the daily
report must never be lost to an inconsistent or incoherent model response.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import Config
from .logging_setup import get_logger, kv

logger = get_logger("humiditydome.llm")

# The exact envelope the model must produce. Kept tiny and stdlib-validatable.
VALID_STATUSES = ("ok", "degraded", "critical")
HEADLINE_MAX = 200

SYSTEM_PROMPT = (
    "You are HumidityDome, a precise infrastructure reporter for a blockchain "
    "operations team. You receive a compact JSON health rollup. "
    "Respond with ONLY a single JSON object, no prose, no markdown code fences, "
    'matching exactly this shape: {"headline": string, "status": string, '
    '"summary": string}. '
    f'"status" must be one of {", ".join(VALID_STATUSES)}. '
    f'"headline" is a one-line summary of at most {HEADLINE_MAX} characters. '
    '"summary" is a concise professional report for a technical operator, under '
    "220 words, using short markdown sections and plain sentences. "
    "Rules: use ONLY the numbers and facts in the JSON rollup; never invent "
    "values; call out outages, version drift, peer problems, sync problems, "
    "clock skew, and seed peering gaps; do not include raw JSON."
)


class LLMError(RuntimeError):
    """Raised when the model response is missing, malformed, or off-schema."""


_FENCE_START = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)
_FENCE_END = re.compile(r"\s*```$")


def _strip_fence(text: str) -> str:
    """Tolerate a model that wraps its JSON in ```json fences."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_START.sub("", stripped)
        stripped = _FENCE_END.sub("", stripped)
    return stripped.strip()


def parse_summary(raw: str) -> dict[str, str]:
    """Parse and validate the model's JSON envelope.

    Returns ``{"headline", "status", "summary"}`` on success; raises
    :class:`LLMError` on anything that is not a well-formed, on-schema object.
    """
    try:
        data = json.loads(_strip_fence(raw))
    except json.JSONDecodeError as exc:
        raise LLMError(f"summary is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise LLMError("summary is not a JSON object")

    headline = data.get("headline")
    status = data.get("status")
    summary = data.get("summary")

    if not isinstance(headline, str) or not headline.strip():
        raise LLMError("missing or invalid 'headline'")
    if len(headline) > HEADLINE_MAX:
        raise LLMError(f"'headline' exceeds {HEADLINE_MAX} characters")
    if status not in VALID_STATUSES:
        raise LLMError(f"invalid 'status': {status!r}")
    if not isinstance(summary, str) or not summary.strip():
        raise LLMError("missing or invalid 'summary'")

    return {"headline": headline.strip(), "status": status, "summary": summary.strip()}


async def summarize(rollup: dict[str, Any], cfg: Config) -> str:
    """Ask the configured model to turn the rollup into a validated summary.

    Returns the ``summary`` markdown on success. Raises :class:`LLMError` if the
    call fails or the response is off-schema; the caller is expected to fall back.
    """
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

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("unexpected gateway response shape") from exc

    if not isinstance(content, str):
        raise LLMError("model content is not a string")

    parsed = parse_summary(content)
    logger.info("llm %s", kv(phase="validated", status=parsed["status"]))
    return parsed["summary"]


def fallback_summary(rollup: dict[str, Any]) -> str:
    """Deterministic summary used when the model is unavailable or off-schema."""
    fleet = rollup.get("fleet", {})
    lines = [
        "HUMIDITYDOME DAILY REPORT (fallback)",
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