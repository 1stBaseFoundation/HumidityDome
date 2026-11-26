"""Daily report compilation."""
from __future__ import annotations

from datetime import UTC, datetime

from humiditydome import report, store
from humiditydome.config import (
    AlertConfig,
    Config,
    EmailConfig,
    LLMConfig,
    PollConfig,
    ReportConfig,
)


def make_cfg(dry_run: bool = True) -> Config:
    return Config(
        poll=PollConfig(),
        report=ReportConfig(),
        alerts=AlertConfig(),
        llm=LLMConfig(),
        email=EmailConfig(),
        nodes=[],
        dry_run=dry_run,
    )


def test_local_date_and_report_time() -> None:
    cfg = make_cfg()
    at_9 = datetime(2026, 11, 23, 7, 0, tzinfo=UTC).timestamp()  # 09:00 UTC+2
    before_9 = datetime(2026, 11, 23, 6, 59, tzinfo=UTC).timestamp()

    assert report.local_date(cfg, at_9) == "2026-11-23"
    assert report.is_report_time(cfg, at_9) is True
    assert report.is_report_time(cfg, before_9) is False


def test_render_html_contains_key_data() -> None:
    rollup = {
        "window": {"start": "2026-11-22T05:00:00Z", "end": "2026-11-23T05:00:00Z"},
        "fleet": {
            "nodes_total": 1,
            "nodes_currently_up": 1,
            "incidents": 0,
            "notes": ["version drift: 1.4.7 (1), 1.4.8 (1)"],
        },
        "nodes": [
            {
                "node": "seed-a",
                "ip": "1.1.1.1",
                "last_state": "ready",
                "uptime_pct": 100.0,
                "peers_avg": 8.0,
                "version": "1.4.7",
                "lat_avg_ms": 20.0,
                "incidents": 0,
            }
        ],
    }

    html = report.render_html(rollup, "All good.", "qwen/qwen3.8-27b")

    assert "seed-a" in html
    assert "All good." in html
    assert "version drift" in html
    assert "qwen/qwen3.8-27b" in html


def test_markdown_to_html() -> None:
    md = "**Fleet Status**\nAll good.\n* one\n* two"
    html_out = report.markdown_to_html(md)

    assert "<h3" in html_out
    assert "Fleet Status" in html_out
    assert "<ul" in html_out
    assert "<li>one</li>" in html_out
    assert "<li>two</li>" in html_out
    assert "**" not in html_out


def test_markdown_inline_bold() -> None:
    assert "<strong>bold</strong>" in report.markdown_to_html("This is **bold** text")


def test_markdown_lists_and_paragraphs() -> None:
    html_out = report.markdown_to_html("**Heading**\n\n* one\n* two\n\nAfter.")

    assert "<h3" in html_out
    assert "<li>one</li>" in html_out
    assert "<li>two</li>" in html_out
    assert "After.</p>" in html_out
    assert "<ul" in html_out
    assert "</ul>" in html_out


def test_markdown_list_at_end_closes() -> None:
    html_out = report.markdown_to_html("* one\n* two")

    assert html_out.endswith("</ul>")


async def test_build_and_send_report_marks_sent(tmp_path) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    cfg = make_cfg(dry_run=True)
    ts = datetime(2026, 11, 23, 5, 0, tzinfo=UTC).timestamp()

    result = await report.build_and_send_report(conn, cfg, now=ts)

    assert result["date"] == "2026-11-23"
    assert store.was_report_sent(conn, "2026-11-23") is True


async def test_build_and_send_report_falls_back_on_llm_error(tmp_path, monkeypatch) -> None:
    conn = store.connect(str(tmp_path / "test.db"))
    cfg = make_cfg(dry_run=False)
    ts = datetime(2026, 11, 23, 5, 0, tzinfo=UTC).timestamp()

    async def boom(rollup, cfg):
        raise report.llm.LLMError("model off-schema")

    async def no_send(to, subject, html_body, cfg):
        return None

    monkeypatch.setattr(report.llm, "summarize", boom)
    monkeypatch.setattr(report.mail, "send_email", no_send)

    result = await report.build_and_send_report(conn, cfg, now=ts)

    # The report still goes out, with the deterministic fallback summary.
    assert "fallback" in result["summary"].lower()
    assert store.was_report_sent(conn, "2026-11-23") is True