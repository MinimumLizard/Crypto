"""Where everything lives on disk.

One module so that a path is never spelled out twice, and so that tests can
point the whole pipeline at a temporary directory by setting TERMINAL_DATA.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONFIG = ROOT / "config"
CONTENT = ROOT / "content"
DOCS = ROOT / "docs"
SITE = ROOT / "site"

# The history store. Overridable so tests never touch real data, and so the
# build workflow can check the `data` branch out somewhere else (D008).
DATA = Path(os.environ.get("TERMINAL_DATA", ROOT / "data"))

RAW = DATA / "raw"                    # raw response cache, keyed by request
OHLCV = DATA / "ohlcv"                # per symbol, per year
SNAPSHOTS = DATA / "snapshots"        # things no API serves historically
MACRO = DATA / "macro"
ONCHAIN = DATA / "onchain"
FUNDAMENTALS = DATA / "fundamentals"
HEALTH = DATA / "source_health.parquet"

# Built artefacts the site reads. Never committed: rebuilt from the store.
ARTEFACTS = SITE / "public" / "data"


def ensure_dirs() -> None:
    for path in (RAW, OHLCV, SNAPSHOTS, MACRO, ONCHAIN, FUNDAMENTALS, ARTEFACTS):
        path.mkdir(parents=True, exist_ok=True)
