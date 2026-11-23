"""Resend email delivery via the connector gateway."""
from __future__ import annotations

from types import SimpleNamespace

from humiditydome import mail
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
        llm=LLMConfig(),
        email=EmailConfig(gateway_base_url="https://gw.example/custom_api/conn"),
        nodes=[],
        dry_run=dry_run,
    )


class _Response:
    def raise_for_status(self) -> None:
        return None


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
        return _Response()


async def test_send_email_posts_to_gateway(monkeypatch) -> None:
    monkeypatch.setattr(mail, "httpx", SimpleNamespace(AsyncClient=_Client))

    await mail.send_email("info@1stbase.org", "Subject", "<p>Body</p>", make_cfg())

    assert _Client.last_url is not None
    assert _Client.last_url.endswith("/emails")
    assert _Client.last_json is not None
    assert _Client.last_json["from"] == "noreply@1stbase.org"
    assert _Client.last_json["to"] == ["info@1stbase.org"]
    assert _Client.last_json["reply_to"] == "info@1stbase.org"
    assert _Client.last_json["subject"] == "Subject"


async def test_send_email_dry_run_is_noop(monkeypatch) -> None:
    called = False

    class _Boom:
        def __init__(self, *args: object, **kwargs: object) -> None:
            nonlocal called
            called = True

    monkeypatch.setattr(mail, "httpx", SimpleNamespace(AsyncClient=_Boom))

    await mail.send_email("info@1stbase.org", "Subject", "<p>Body</p>", make_cfg(dry_run=True))

    assert called is False