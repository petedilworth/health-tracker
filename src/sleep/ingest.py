"""Data-layer orchestration: pull from Oura, transform, upsert into history.

Stage 1 scope. Metrics, site and email arrive in later stages; this module is
responsible only for getting correct raw data into `data/history.csv`.
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from . import config, store, transform
from .client import OuraClient

log = logging.getLogger("sleep.ingest")

# Oura caps the span of a single request; walk long ranges in chunks.
CHUNK_DAYS = 180
# How far back the daily job re-pulls. Generous enough to catch late-synced
# nights and backfill gaps; upsert makes the overlap harmless.
DEFAULT_LOOKBACK_DAYS = 30


def _chunks(start: dt.date, end: dt.date, size: int):
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=size - 1), end)
        yield cur, chunk_end
        cur = chunk_end + dt.timedelta(days=1)


def ingest_range(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Pull a date range from Oura and merge it into the stored history."""
    settings = config.load_settings(require_oura=True)
    config.ensure_dirs()

    client = OuraClient(settings.oura_pat, verify_tls=settings.verify_tls)
    history = store.load_history(config.HISTORY_PATH)
    before = len(history)

    for chunk_start, chunk_end in _chunks(start, end, CHUNK_DAYS):
        log.info("Pulling %s -> %s", chunk_start, chunk_end)
        payloads = client.fetch_all(chunk_start, chunk_end)
        log.info(
            "  sleep=%d daily_sleep=%d activity=%d readiness=%d",
            len(payloads["sleep"]), len(payloads["daily_sleep"]),
            len(payloads["daily_activity"]), len(payloads["daily_readiness"]),
        )
        rows = transform.build_rows(payloads)
        history = store.upsert(history, rows)

    store.save_history(history, config.HISTORY_PATH)
    log.info("History now holds %d nights (was %d) -> %s",
             len(history), before, config.HISTORY_PATH)
    return history


def ingest_recent(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> pd.DataFrame:
    end = dt.date.today()
    return ingest_range(end - dt.timedelta(days=lookback_days), end)
