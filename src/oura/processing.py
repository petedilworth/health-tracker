"""Transform raw Oura API records into the analytics DataFrame.

Design notes
------------
* Bedtime is converted to a *decimal hour* with a +24 shift for after-midnight
  times, so 1:00am becomes 25.0 rather than 1.0. Without this, averaging an
  11pm bedtime (23.0) with a 1am bedtime (1.0) would wrongly give noon (12.0).
* "Percentile" here means today's value ranked against ALL history of the same
  kind — daily value vs all daily values, 7-day avg vs all 7-day avgs, etc.
  It is NOT a percentile within a rolling window.
"""

from __future__ import annotations

import pandas as pd

# The two metrics we track. Add to this list to extend the dashboard later.
METRICS = ["score", "bedtime"]


def _bedtime_to_decimal_hour(bedtime_start: pd.Series) -> pd.Series:
    """ISO datetime string -> decimal hour, +24 for after-midnight times."""
    ts = pd.to_datetime(bedtime_start, utc=False, errors="coerce")
    hours = ts.dt.hour + ts.dt.minute / 60 + ts.dt.second / 3600
    # Evenings stay as-is (22, 23); after-midnight (0-11) shifts to 24-35.
    return hours.where(hours >= 12, hours + 24)


def daily_sleep_to_frame(records: list[dict]) -> pd.DataFrame:
    """daily_sleep records -> DataFrame[day, score]."""
    if not records:
        return pd.DataFrame(columns=["day", "score"])
    df = pd.DataFrame(records)[["day", "score"]]
    df["day"] = pd.to_datetime(df["day"]).dt.date
    return df


def sleep_sessions_to_frame(records: list[dict]) -> pd.DataFrame:
    """sleep records -> DataFrame[day, bedtime], long_sleep only (no naps)."""
    if not records:
        return pd.DataFrame(columns=["day", "bedtime"])
    df = pd.DataFrame(records)
    if "type" in df.columns:
        df = df[df["type"] == "long_sleep"].copy()
    df["day"] = pd.to_datetime(df["day"]).dt.date
    df["bedtime"] = _bedtime_to_decimal_hour(df["bedtime_start"])
    # If a day somehow has two long sleeps, keep the earliest bedtime.
    df = df.sort_values("bedtime").drop_duplicates("day", keep="first")
    return df[["day", "bedtime"]]


def merge_metrics(sleep_df: pd.DataFrame, score_df: pd.DataFrame) -> pd.DataFrame:
    """Combine sleep sessions (base) with scores on `day`."""
    merged = sleep_df.merge(score_df, on="day", how="left")
    return merged.sort_values("day").reset_index(drop=True)


def add_rolling_and_percentiles(df: pd.DataFrame) -> pd.DataFrame:
    """Add 7d/30d trailing averages and all-history percentile ranks.

    For each metric M this adds: M_7d, M_30d, and M_pct_daily / M_pct_7d /
    M_pct_30d (0-100). Percentile rank = share of historical values <= this one.
    """
    df = df.sort_values("day").reset_index(drop=True)
    for m in METRICS:
        if m not in df.columns:
            continue
        df[f"{m}_7d"] = df[m].rolling(window=7, min_periods=1).mean()
        df[f"{m}_30d"] = df[m].rolling(window=30, min_periods=1).mean()
        # rank(pct=True) gives each value's fraction of the distribution.
        df[f"{m}_pct_daily"] = df[m].rank(pct=True) * 100
        df[f"{m}_pct_7d"] = df[f"{m}_7d"].rank(pct=True) * 100
        df[f"{m}_pct_30d"] = df[f"{m}_30d"].rank(pct=True) * 100
    return df


def build_raw_frame(daily_sleep_records: list[dict],
                    sleep_records: list[dict]) -> pd.DataFrame:
    """Raw API records -> merged DataFrame[day, bedtime, score] (no derived cols).

    Rolling averages and percentiles are intentionally NOT added here: they must
    be computed over the full stored history, not just a freshly pulled window.
    """
    score_df = daily_sleep_to_frame(daily_sleep_records)
    sleep_df = sleep_sessions_to_frame(sleep_records)
    return merge_metrics(sleep_df, score_df)
