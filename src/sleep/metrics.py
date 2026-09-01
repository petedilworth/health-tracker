"""Shared analytics: daily reindexing, rolling averages, percentiles, aggregation.

One thing here matters more than it looks: everything works on a **calendar-day**
index, not a row index. This history has 159 gaps, so "the last 30 rows" could
span 40 calendar days. Reindexing to a continuous daily index makes a 30-day
average genuinely mean 30 days, and makes the coverage rule meaningful.
"""

from __future__ import annotations

import pandas as pd

# A rolling average is suppressed unless this share of the window has data, so a
# "30-day average" is never quietly computed from three nights.
MIN_COVERAGE = 0.7

PERIODS = {"weekly": "W-MON", "monthly": "MS", "quarterly": "QS", "annual": "YS"}


def to_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Reindex to a continuous daily DatetimeIndex, inserting empty rows for gaps."""
    if df.empty:
        return df.set_index(pd.DatetimeIndex([], name="day"))
    out = df.copy()
    out["day"] = pd.to_datetime(out["day"])
    out = out.drop_duplicates("day").set_index("day").sort_index()
    full = pd.date_range(out.index.min(), out.index.max(), freq="D", name="day")
    return out.reindex(full)


def rolling_mean(series: pd.Series, window: int,
                 min_coverage: float = MIN_COVERAGE) -> pd.Series:
    """Trailing mean over `window` calendar days, blanked on thin coverage."""
    mean = series.rolling(window, min_periods=1).mean()
    covered = series.notna().rolling(window, min_periods=1).sum()
    return mean.where(covered >= window * min_coverage)


def percentile_rank(series: pd.Series) -> pd.Series:
    """Each value's percentile (0-100) within the whole series.

    Full-history rather than trailing: the question being answered is "where
    does this night sit against everything I've ever recorded". Because every
    metric is recomputed from full history on each run, these stay consistent.
    """
    return series.rank(pct=True) * 100


def add_rollups(daily: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Add `_7d`, `_30d` and percentile columns for each metric.

    Built as one block and concatenated once — adding ~100 columns individually
    fragments the frame badly.
    """
    new: dict[str, pd.Series] = {}
    for col in columns:
        if col not in daily.columns:
            continue
        avg_7d = rolling_mean(daily[col], 7)
        avg_30d = rolling_mean(daily[col], 30)
        new[f"{col}_7d"] = avg_7d
        new[f"{col}_30d"] = avg_30d
        new[f"{col}_pct"] = percentile_rank(daily[col])
        new[f"{col}_pct_7d"] = percentile_rank(avg_7d)
        new[f"{col}_pct_30d"] = percentile_rank(avg_30d)
    if not new:
        return daily.copy()
    return pd.concat([daily, pd.DataFrame(new, index=daily.index)], axis=1)


def aggregate(daily: pd.DataFrame, column: str, period: str) -> pd.DataFrame:
    """Average a metric per week / month / quarter / year.

    Returns columns: period_start, value, n (nights contributing).
    """
    if column not in daily.columns or daily.empty:
        return pd.DataFrame(columns=["period_start", "value", "n"])
    freq = PERIODS.get(period)
    if freq is None:
        raise ValueError(f"Unknown period '{period}'. Use one of {list(PERIODS)}.")
    grouped = daily[column].resample(freq)
    out = pd.DataFrame({"value": grouped.mean(), "n": grouped.count()})
    out = out[out["n"] > 0].reset_index()
    return out.rename(columns={"day": "period_start"})


def top_bottom(daily: pd.DataFrame, column: str, n: int = 10,
               since: pd.Timestamp | None = None,
               higher_is_better: bool = True) -> dict[str, pd.DataFrame]:
    """Best and worst n nights for a metric, optionally within a period."""
    if column not in daily.columns:
        return {"top": pd.DataFrame(), "bottom": pd.DataFrame()}
    s = daily[column].dropna()
    if since is not None:
        s = s[s.index >= since]
    if s.empty:
        return {"top": pd.DataFrame(), "bottom": pd.DataFrame()}

    high = s.nlargest(n).rename("value").reset_index()
    low = s.nsmallest(n).rename("value").reset_index()
    # "Top" means best, which for e.g. resting HR is the lowest value.
    return {"top": high, "bottom": low} if higher_is_better else {"top": low, "bottom": high}
