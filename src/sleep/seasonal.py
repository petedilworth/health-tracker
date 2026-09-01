"""Drift-aware baselines: rolling window minus learned calendar-month effect.

The backfill showed real longitudinal drift in this dataset — HRV median moves
51 -> 45 -> 51 across the years, respiratory rate 13.38 -> 12.75. Comparing a
night against all-history would therefore mostly measure "which year was this",
not "how was last night".

So each value is compared against a *trailing* baseline, after subtracting the
average effect of the calendar month learned from the full history. That
separates "it's winter" from "I'm run down", which is the whole point of the
seasonal adjustment.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BASELINE_WINDOW_DAYS = 90
MIN_BASELINE_OBS = 20
# Metrics that drift with fitness, age and season, and so need this treatment.
DRIFT_PRONE = ["hrv", "hr_low", "hr_avg", "breaths_per_min", "temp_deviation"]


def month_effect(daily: pd.DataFrame, column: str) -> pd.Series:
    """Average deviation from the overall mean, per calendar month (1-12).

    Learned from full history, so it reflects genuine seasonality rather than
    whatever happened to be true recently.
    """
    s = daily[column].dropna()
    if s.empty:
        return pd.Series(0.0, index=range(1, 13))
    overall = s.mean()
    by_month = s.groupby(s.index.month).mean() - overall
    return by_month.reindex(range(1, 13)).fillna(0.0)


def detrend(daily: pd.DataFrame, column: str) -> pd.Series:
    """Remove the calendar-month effect from a series."""
    effect = month_effect(daily, column)
    return daily[column] - daily.index.month.map(effect)


def rolling_z(daily: pd.DataFrame, column: str,
              window: int = BASELINE_WINDOW_DAYS) -> pd.Series:
    """De-trended value expressed as a z-score against its trailing baseline.

    Positive means "higher than my recent normal for this time of year".
    The baseline is shifted by one day so tonight never informs its own
    expectation.
    """
    if column not in daily.columns:
        return pd.Series(np.nan, index=daily.index)

    adjusted = detrend(daily, column)
    prior = adjusted.shift(1)
    mean = prior.rolling(window, min_periods=MIN_BASELINE_OBS).mean()
    std = prior.rolling(window, min_periods=MIN_BASELINE_OBS).std()
    # A flat baseline would divide by ~0 and explode; treat it as "no signal".
    std = std.where(std > 1e-6)
    return (adjusted - mean) / std


def add_z_scores(daily: pd.DataFrame,
                 columns: list[str] | None = None) -> pd.DataFrame:
    """Attach a `<column>_z` series for each drift-prone metric."""
    out = daily.copy()
    for col in (columns if columns is not None else DRIFT_PRONE):
        if col in out.columns:
            out[f"{col}_z"] = rolling_z(out, col)
    return out
