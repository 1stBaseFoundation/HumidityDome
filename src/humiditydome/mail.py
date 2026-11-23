"""Resend email delivery via the Sprites connector gateway."""
from __future__ import annotations

import httpx

from .config import Config


async def send_email(to: str, subject: str, html_body: str, cfg: Config) -> None:
    """Send one email. In dry-run mode this is a no-op."""
    if cfg.dry_run:
        return

    url = f"{cfg.email.gateway_base_url.rstrip('/')}/emails"
    body: dict[str, object] = {
        "from": cfg.report.from_email,
        "to": [to],
        "subject": subject,
        "html": html_body,
    }
    if cfg.report.reply_to:
        body["reply_to"] = cfg.report.reply_to

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, json=body)
        response.raise_for_status()