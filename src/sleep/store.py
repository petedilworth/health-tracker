"""CSV history with idempotent upserts, plus the exclusions list.

The daily job pulls only a recent window, but percentiles and baselines need the
full history — so everything lives in one CSV and new rows are merged in by
`day`. Upserting rather than appending means re-running a day never duplicates
or double-counts it.

Only raw measurements are stored. Rolling averages, percentiles and the sleep
score are recomputed from full history on every run, so they always reflect the
current exclusions list.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from .schema import EXCLUSION_COLUMNS, RAW_COLUMNS

log = logging.getLogger("sleep.store")


# --- history ----------------------------------------------------------------

def load_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=RAW_COLUMNS)
    df = pd.read_csv(path)
    if "day" in df.columns:
        df["day"] = pd.to_datetime(df["day"]).dt.date
    for col in RAW_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[RAW_COLUMNS]


def upsert(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Merge freshly pulled rows into history, field by field.

    New values win where present; existing values survive where the new row is
    empty. Replacing a day's row wholesale would let one endpoint hiccup (say,
    daily_readiness returning nothing for a day that already had temperature
    data) silently erase stored fields — and once the day left the re-pull
    window, permanently.
    """
    if new.empty:
        return existing.sort_values("day").reset_index(drop=True) if not existing.empty else existing
    if existing.empty:
        return new.sort_values("day").reset_index(drop=True)

    old_indexed = existing.set_index("day")
    new_indexed = new.drop_duplicates("day", keep="last").set_index("day")
    combined = new_indexed.combine_first(old_indexed)
    return combined.sort_index().reset_index()


def save_history(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    for col in RAW_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    out[RAW_COLUMNS].to_csv(path, index=False)


# --- exclusions -------------------------------------------------------------

def load_exclusions(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=EXCLUSION_COLUMNS)
    df = pd.read_csv(path)
    for col in EXCLUSION_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    if not df.empty:
        df["day"] = pd.to_datetime(df["day"]).dt.date
    return df[EXCLUSION_COLUMNS]


def save_exclusions(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df[EXCLUSION_COLUMNS].sort_values("day").to_csv(path, index=False)


def add_exclusion(path: Path, day: dt.date, reason: str) -> pd.DataFrame:
    """Exclude a day. Re-excluding an existing day updates its reason."""
    existing = load_exclusions(path)
    row = pd.DataFrame([{
        "day": day,
        "reason": reason or "unspecified",
        "added_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }])
    combined = pd.concat([existing, row], ignore_index=True)
    combined = combined.drop_duplicates("day", keep="last")
    save_exclusions(combined, path)
    return combined


def remove_exclusion(path: Path, day: dt.date) -> pd.DataFrame:
    """Un-exclude a day, bringing it back into all metrics."""
    existing = load_exclusions(path)
    if existing.empty:
        return existing
    combined = existing[existing["day"] != day].reset_index(drop=True)
    save_exclusions(combined, path)
    return combined


def apply_exclusions(history: pd.DataFrame, exclusions: pd.DataFrame) -> pd.DataFrame:
    """Drop excluded days so they never reach metrics, percentiles or charts."""
    if history.empty or exclusions.empty:
        return history
    excluded = set(exclusions["day"])
    kept = history[~history["day"].isin(excluded)].reset_index(drop=True)
    log.info("Excluded %d day(s) from %d rows", len(history) - len(kept), len(history))
    return kept
