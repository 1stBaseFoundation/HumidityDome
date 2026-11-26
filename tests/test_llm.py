"""LLM summary via the connector gateway."""
from __future__ import annotations

import json
import json as json_mod
from types import SimpleNamespace

import pytest

from humiditydome import llm
from humiditydome.config import (
    AlertConfig,
    Config,
    EmailConfig,
    LLMConfig,
    PollConfig,
    ReportConfig,
)


def make_cfg(dry_run: bool = False) -> Config:
    return Config(
        poll=PollConfig(),
        report=ReportConfig(),
        alerts=AlertConfig(),
        llm=LLMConfig(gateway_base_url="https://gw.example/custom_api/conn"),
        email=EmailConfig(),
        nodes=[],
        dry_run=dry_run,
    )


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Client:
    last_url: str | None = None
    last_json: dict | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        return None

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, json: dict) -> _Response:
        _Client.last_url = url
        _Client.last_json = json
        content = json_mod.dumps(
            {"headline": "All good", "status": "ok", "summary": "Summary text"}
        )
        return _Response({"choices": [{"message": {"content": content}}]})


async def test_summarize_calls_gateway(monkeypatch) -> None:
    monkeypatch.setattr(llm, "httpx", SimpleNamespace(AsyncClient=_Client))

    out = await llm.summarize({"fleet": {"nodes_total": 5}}, make_cfg())

    assert out == "Summary text"
    assert _Client.last_url is not None
    assert _Client.last_url.endswith("/openai/v1/chat/completions")
    assert _Client.last_json is not None
    assert _Client.last_json["model"] == "qwen/qwen3.8-27b"


async def test_summarize_dry_run_uses_fallback() -> None:
    out = await llm.summarize(
        {"fleet": {"nodes_total": 5}, "nodes": []}, make_cfg(dry_run=True)
    )
    assert "HUMIDITYDOME DAILY REPORT (fallback)" in out


def test_parse_summary_valid() -> None:
    raw = '{"headline": "h", "status": "degraded", "summary": "body"}'
    parsed = llm.parse_summary(raw)
    assert parsed == {"headline": "h", "status": "degraded", "summary": "body"}


def test_parse_summary_strips_code_fence() -> None:
    raw = '```json\n{"headline": "h", "status": "ok", "summary": "b"}\n```'
    assert llm.parse_summary(raw)["status"] == "ok"


def test_parse_summary_rejects_non_json() -> None:
    with pytest.raises(llm.LLMError):
        llm.parse_summary("not json at all")


def test_parse_summary_rejects_bad_status() -> None:
    raw = '{"headline": "h", "status": "fine", "summary": "b"}'
    with pytest.raises(llm.LLMError):
        llm.parse_summary(raw)


def test_parse_summary_rejects_empty_summary() -> None:
    raw = '{"headline": "h", "status": "ok", "summary": "   "}'
    with pytest.raises(llm.LLMError):
        llm.parse_summary(raw)


def test_parse_summary_rejects_overlong_headline() -> None:
    raw = json.dumps({"headline": "x" * 500, "status": "ok", "summary": "b"})
    with pytest.raises(llm.LLMError):
        llm.parse_summary(raw)


async def test_summarize_raises_on_off_schema(monkeypatch) -> None:
    class _BadClient(_Client):
        async def post(self, url: str, json: dict) -> _Response:
            return _Response({"choices": [{"message": {"content": "garbage"}}]})

    monkeypatch.setattr(llm, "httpx", SimpleNamespace(AsyncClient=_BadClient))

    with pytest.raises(llm.LLMError):
        await llm.summarize({"fleet": {"nodes_total": 5}}, make_cfg())