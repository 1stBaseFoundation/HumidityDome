"""Logging configuration for HumidityDome.

Emits structured ``key=value`` lines so a run can be traced end-to-end. The
level is read from ``HUMIDITYDOME_LOG_LEVEL`` (default ``INFO``).

A suspended or crashed run is diagnosable from the *last* line it emitted: each
phase of :func:`humiditydome.app.run_tick` logs a boundary line, so a missing
completion line points at the phase that was interrupted.
"""
from __future__ import annotations

import logging
import os
import sys

_LEVEL_ENV = "HUMIDITYDOME_LOG_LEVEL"
_DEFAULT_LEVEL = "INFO"
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _resolve_level() -> int:
    """Map the env var to a logging level, falling back to INFO."""
    name = os.environ.get(_LEVEL_ENV, _DEFAULT_LEVEL).strip().upper()
    level = logging.getLevelNamesMapping().get(name)
    return level if level is not None else logging.INFO


def configure_logging() -> None:
    """Install a stdout root handler once. Safe to call repeatedly."""
    root = logging.getLogger()
    root.setLevel(_resolve_level())
    if not any(getattr(h, "_humiditydome", False) for h in root.handlers):
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler._humiditydome = True  # type: ignore[attr-defined]
        root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def kv(**fields: object) -> str:
    """Render fields as a ``key=value`` string, quoting values with spaces."""
    parts: list[str] = []
    for key, value in fields.items():
        text = "" if value is None else str(value)
        parts.append(f'{key}="{text}"' if " " in text else f"{key}={text}")
    return " ".join(parts)