"""Shared test environment.

No real secrets or network: dry-run is forced and the app uses a temp SQLite file.
"""
from __future__ import annotations

import os
import tempfile

_DB_DIR = tempfile.mkdtemp(prefix="humiditydome-pytest-")
os.environ.setdefault("HUMIDITYDOME_DRYRUN", "1")
os.environ.setdefault("HUMIDITYDOME_DB", os.path.join(_DB_DIR, "test.db"))