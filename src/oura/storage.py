"""Persist the analytics history to CSV with idempotent upserts.

The daily job pulls only a recent window from the API, but percentiles need the
full history — so we keep everything in one CSV and merge new rows in by `day`.
Upserting (rather than appending) means re-running the job never duplicates data.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# Only the raw metric columns are stored; rolling avgs and percentiles are
# recomputed from the full history on every run so they're always current.
RAW_COLUMNS = ["day", "bedtime", "score"]


def load_history(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        return pd.DataFrame(columns=RAW_COLUMNS)
    df = pd.read_csv(csv_path)
    df["day"] = pd.to_datetime(df["day"]).dt.date
    return df


def upsert(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Merge new rows into existing history, newest values winning on conflict."""
    cols = [c for c in RAW_COLUMNS if c in new.columns or c in existing.columns]
    combined = pd.concat([existing, new[[c for c in cols if c in new.columns]]],
                         ignore_index=True)
    # Later rows (the freshly pulled ones) win when a `day` appears twice.
    combined = combined.drop_duplicates("day", keep="last")
    return combined.sort_values("day").reset_index(drop=True)


def save_history(df: pd.DataFrame, csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df[[c for c in RAW_COLUMNS if c in df.columns]].to_csv(csv_path, index=False)
