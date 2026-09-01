"""Data-quality gates: value clamping, reliable-window detection, anomalies.

Three separate jobs, deliberately kept apart:

1. `clamp_implausible` nulls individual *values* that are physiologically
   impossible, leaving the rest of the night intact. One bad thermistor reading
   shouldn't discard an otherwise good night.
2. `detect_reliable_start` finds where a metric's history becomes credible, so
   a bad early era (Oura recorded almost no steps before 2021) doesn't poison
   that metric's baselines and percentiles.
3. `find_anomalies` flags nights worth a human look. It never deletes anything —
   exclusion stays an explicit decision recorded in data/exclusions.csv.
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd

log = logging.getLogger("sleep.quality")

# Physiologically plausible ranges. Anything outside is a sensor artifact, not a
# remarkable night. Bounds are deliberately wide — this catches impossibilities,
# not merely unusual values, which are the whole point of the analysis.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "temp_deviation": (-2.0, 2.0),
    "temp_trend_deviation": (-2.0, 2.0),
    "hrv": (5.0, 200.0),
    "hr_low": (30.0, 100.0),
    "hr_avg": (30.0, 120.0),
    "breaths_per_min": (8.0, 25.0),
    "efficiency": (30.0, 100.0),
    "total_sleep_h": (0.0, 14.0),
    "time_in_bed_h": (0.0, 16.0),
    "deep_h": (0.0, 6.0),
    "rem_h": (0.0, 6.0),
    "light_h": (0.0, 10.0),
    "latency_min": (0.0, 240.0),
    "bedtime": (17.0, 32.0),      # 5pm to 8am
    "nap_sleep_h": (0.0, 8.0),
    "steps": (0.0, 60000.0),
}

DST_TOLERANCE = 0.1     # a clock change shifts the span by exactly 1.0h
SHORT_SLEEP_H = 4.0
LONG_SLEEP_H = 11.0
LOW_EFFICIENCY = 65.0
BEDTIME_JUMP_H = 4.0


def clamp_implausible(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Null values outside physiological range. Returns (frame, counts dropped)."""
    out = df.copy()
    dropped: dict[str, int] = {}
    for col, (lo, hi) in PLAUSIBLE_RANGES.items():
        if col not in out.columns:
            continue
        values = pd.to_numeric(out[col], errors="coerce")
        bad = values.notna() & ~values.between(lo, hi)
        if bad.any():
            dropped[col] = int(bad.sum())
            out.loc[bad, col] = np.nan
            log.info("Clamped %d implausible %s value(s)", int(bad.sum()), col)
    return out, dropped


def detect_reliable_start(
    df: pd.DataFrame, column: str, window: int = 28,
    min_fraction: float = 0.2,
) -> dt.date | None:
    """First day from which `column` looks credible, and stays that way.

    Finds the earliest day whose trailing `window`-day median exceeds
    `min_fraction` of the series' overall median, with no later relapse below
    it. Oura logged almost no steps before 2021 (2020 median: 137/day), which
    would otherwise distort every steps percentile. Deriving the boundary from
    the data avoids hardcoding a date and protects any other metric that turns
    out to have a bad early era.

    Returns None when the whole series is credible (the common case).
    """
    if column not in df.columns or df.empty:
        return None
    s = pd.to_numeric(df.set_index("day")[column], errors="coerce")
    if s.notna().sum() < window:
        return None

    overall = s.median()
    if not np.isfinite(overall) or overall <= 0:
        return None

    threshold = overall * min_fraction
    rolling = s.rolling(window, min_periods=max(3, window // 4)).median()
    # Rows before the window fills have no rolling value. That means "not yet
    # evaluated", not "not credible" — treating them as failures would hand back
    # a start date for every series, including perfectly clean ones.
    rolling = rolling.dropna()
    if rolling.empty:
        return None

    credible = rolling >= threshold
    if credible.all():
        return None

    # The start is the first day after which credibility never lapses again.
    last_bad = credible[~credible].index.max()
    after = credible[credible.index > last_bad]
    if after.empty:
        return None
    start = after.index.min()

    # Guard: this detector exists to cut a bad *early era*, not to react to a
    # recent slump. If most days before the proposed start were actually fine —
    # e.g. an injury month years into good data — truncating there would erase
    # good history, so refuse and keep everything.
    before = credible[credible.index < start]
    if len(before) and before.mean() > 0.5:
        log.info("Ignoring candidate reliable start for %s at %s: %.0f%% of the "
                 "earlier era is credible", column, start, before.mean() * 100)
        return None
    log.info("Reliable start for %s detected at %s (median %.0f, threshold %.0f)",
             column, start, overall, threshold)
    return start


def apply_reliable_starts(df: pd.DataFrame,
                          columns: list[str]) -> tuple[pd.DataFrame, dict[str, dt.date]]:
    """Null a metric's values before its detected reliable start."""
    out = df.copy()
    starts: dict[str, dt.date] = {}
    for col in columns:
        start = detect_reliable_start(out, col)
        if start is None:
            continue
        starts[col] = start
        before = out["day"] < start
        out.loc[before, col] = np.nan
        log.info("Nulled %d %s value(s) before %s", int(before.sum()), col, start)
    return out, starts


# --- anomaly detection ------------------------------------------------------

def find_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Flag nights worth a human look. Returns one row per flagged night.

    Columns: day, reasons (list), severity, plus the values that triggered it.
    Nothing is removed here — this only builds the review queue.
    """
    if df.empty:
        return pd.DataFrame(columns=["day", "reasons", "severity", "benign"])

    d = df.sort_values("day").reset_index(drop=True).copy()
    span = d["waketime"] - d["bedtime"]
    mismatch = span - d["time_in_bed_h"]
    bed_median = d["bedtime"].rolling(7, min_periods=3).median()

    # A benign clock change must look like one on the calendar too: exactly
    # ±1h AND in a month DST actually transitions (March for both EU and US,
    # October EU, November US — this dataset contains both). A 1-hour recording
    # discrepancy in July is a real anomaly, not a clock change.
    is_1h = mismatch.abs().sub(1.0).abs() <= DST_TOLERANCE
    months = pd.to_datetime(pd.Series(d["day"])).dt.month
    dst_month = months.isin([3, 10, 11])
    is_dst = is_1h & dst_month

    checks: dict[str, pd.Series] = {
        "waketime encoding broken (span ≈ -24h)": mismatch < -20,
        "DST clock change (±1h) — benign": is_dst,
        "span/duration mismatch >0.5h": (
            (mismatch.abs() > 0.5)
            & ~is_dst
            & (mismatch > -20)
        ),
        f"very short sleep (<{SHORT_SLEEP_H:g}h)": d["total_sleep_h"] < SHORT_SLEEP_H,
        f"long sleep (>{LONG_SLEEP_H:g}h)": d["total_sleep_h"] > LONG_SLEEP_H,
        f"low efficiency (<{LOW_EFFICIENCY:g}%)": d["efficiency"] < LOW_EFFICIENCY,
        f"bedtime jump >{BEDTIME_JUMP_H:g}h vs 7-night median": (
            (d["bedtime"] - bed_median).abs() > BEDTIME_JUMP_H
        ),
        "implausible temperature": ~d["temp_deviation"].between(-2, 2) & d["temp_deviation"].notna(),
        "implausible HRV": ~d["hrv"].between(15, 120) & d["hrv"].notna(),
        "implausible resting HR": ~d["hr_low"].between(38, 75) & d["hr_low"].notna(),
    }

    rows = []
    for i in range(len(d)):
        reasons = [name for name, mask in checks.items()
                   if bool(mask.fillna(False).iloc[i])]
        if not reasons:
            continue
        # A night explained solely by a clock change needs no decision from you.
        benign = all("benign" in r for r in reasons)
        rows.append({
            "day": d.at[i, "day"],
            "reasons": "; ".join(reasons),
            "n_reasons": len(reasons),
            "benign": benign,
            "bedtime": d.at[i, "bedtime"],
            "waketime": d.at[i, "waketime"],
            "total_sleep_h": d.at[i, "total_sleep_h"],
            "time_in_bed_h": d.at[i, "time_in_bed_h"],
            "efficiency": d.at[i, "efficiency"],
            "hrv": d.at[i, "hrv"],
            "hr_low": d.at[i, "hr_low"],
            "temp_deviation": d.at[i, "temp_deviation"],
            "steps": d.at[i, "steps"],
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["day", "reasons", "n_reasons", "benign"])
    return out.sort_values(["benign", "n_reasons", "day"],
                           ascending=[True, False, True]).reset_index(drop=True)
