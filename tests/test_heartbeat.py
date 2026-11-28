"""Dead-man's-switch pings after a completed tick."""
from __future__ import annotations

from types import SimpleNamespace

import httpx

from humiditydome import heartbeat


class _Response:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPError("boom")


class _Client:
    pings: list[str] = []
    status_code = 200

    def __init__(self, *args: object, **kwargs: object) -> None:
        return None

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def get(self, url: str) -> _Response:
        _Client.pings.append(url)
        return _Response(_Client.status_code)


async def test_ping_skips_when_unset(monkeypatch) -> None:
    monkeypatch.setattr(heartbeat, "httpx", SimpleNamespace(AsyncClient=_Client))
    _Client.pings = []

    assert await heartbeat.ping("") is False
    assert _Client.pings == []


async def test_ping_success(monkeypatch) -> None:
    monkeypatch.setattr(heartbeat, "httpx", SimpleNamespace(AsyncClient=_Client))
    _Client.pings = []
    _Client.status_code = 200

    assert await heartbeat.ping("https://hc.example/uuid") is True
    assert _Client.pings == ["https://hc.example/uuid"]


async def test_ping_failure_appends_fail(monkeypatch) -> None:
    monkeypatch.setattr(heartbeat, "httpx", SimpleNamespace(AsyncClient=_Client))
    _Client.pings = []
    _Client.status_code = 200

    assert await heartbeat.ping("https://hc.example/uuid/", ok=False) is True
    assert _Client.pings == ["https://hc.example/uuid/fail"]


async def test_ping_swallows_http_errors(monkeypatch) -> None:
    monkeypatch.setattr(heartbeat, "httpx", SimpleNamespace(AsyncClient=_Client))
    _Client.pings = []
    _Client.status_code = 500

    assert await heartbeat.ping("https://hc.example/uuid") is False
