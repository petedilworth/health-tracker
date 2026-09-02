"""The sleep score and its family: need, debt, performance, readiness.

Design principle throughout: **every component is graded against your own
history**, never an absolute target. Independent validation shows Oura's
stage-level measurements carry systematic bias — but a roughly constant bias
cancels out when comparing you against yourself, which is what makes the
low-confidence components usable at all. It also fixes the clustering that made
Oura's own score uninformative: percentile scoring uses the whole 0-100 range by
construction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import metrics
from .schema import SCORE_COMPONENTS, TOTAL_SCORE_WEIGHT

# --- sleep need -------------------------------------------------------------
# Which percentile of your own sleep stands in for "unrestricted" need.
#
# Chosen 2026-09: P75 (~7.3h here) over P90 (~7.8h). At P90 the need was met on
# only 9.3% of nights — a target hit one night in eleven stops being believed.
# P75 lifts that to 22.5% while still leaving a real nightly gap.
#
# The trade-off, recorded for the review: if true requirement is nearer 7.8h,
# P75 encodes part of a chronic restriction as the requirement and will
# under-report debt. Revisited annually by the recalibration-review workflow,
# first firing 2027-03-01.
NEED_QUANTILE = 0.75
NEED_MIN_H, NEED_MAX_H = 6.0, 10.0

# --- duration scoring -------------------------------------------------------
# How sleep duration maps to 0-100 against need. Piecewise, because a single
# linear ramp capped at need piles nights up on the ceiling: need *is* your P75,
# so you meet or beat it a quarter of the time by construction, and 26% of all
# nights scored exactly 100 — the same compression that made Oura's score
# uninformative. Meeting need is now a strong 85; the last 15 points are
# reserved for genuinely exceeding it.
DURATION_FLOOR_RATIO = 0.65     # ≤65% of need scores 0
DURATION_NEED_SCORE = 85.0      # hitting need exactly
DURATION_CEILING_RATIO = 1.20   # ≥120% of need scores 100
DEBT_UPLIFT_PER_HOUR = 0.15
DEBT_UPLIFT_CAP_H = 1.0
ACTIVITY_UPLIFT_H = 0.25
ACTIVITY_QUANTILE = 0.80

# --- debt -------------------------------------------------------------------
DEBT_TAU_DAYS = 7.0          # half-life ~4.9 days

# --- readiness --------------------------------------------------------------
# (source column, weight, higher_is_better). Everything is converted to a
# 0-100 "good" scale first, so weights stay positive and the mix is a plain
# weighted average.
READINESS_PARTS = [
    ("hrv_z", 0.35, True),
    ("hr_low_z", 0.25, False),
    ("breaths_per_min_z", 0.15, False),
    ("__score__", 0.25, True),
]

TIMING_WINDOW_NIGHTS = 7


def _z_to_percentile(z: pd.Series) -> pd.Series:
    """Normal CDF without a scipy dependency (erf is in the stdlib's math)."""
    from math import erf, sqrt
    return z.apply(
        lambda v: np.nan if pd.isna(v) else 100.0 * 0.5 * (1.0 + erf(v / sqrt(2.0)))
    )


# --- sleep need, debt, performance -----------------------------------------

def sleep_need(daily: pd.DataFrame) -> pd.Series:
    """The stable sleep-need baseline, in hours.

    Your own longer natural nights — the NEED_QUANTILE of *all* recorded sleep —
    standing in for "unrestricted" sleep, since alarm-free mornings can't be
    detected. One flat number, recomputed as history accumulates.

    Deliberately not a rolling window. A trailing window tracks recent
    behaviour, so a stretch of poor sleep quietly lowers the bar it is judged
    against: this dataset's need had drifted 7.27h -> 6.95h following a slipping
    2026, making a bad run look better than it was. The same class of
    self-defeating feedback as the debt loop fixed earlier, one layer up.

    Also not adjusted for debt or activity — those uplifts live in
    `sleep_recommended_h`. This baseline is what debt accounting and
    performance % grade against.
    """
    total = daily["total_sleep_h"]
    if not total.notna().any():
        return pd.Series(8.0, index=daily.index)
    baseline = float(np.clip(total.quantile(NEED_QUANTILE), NEED_MIN_H, NEED_MAX_H))
    return pd.Series(baseline, index=daily.index)


def duration_score(ratio: float | pd.Series) -> pd.Series:
    """Map slept/need to 0-100. See the DURATION_* constants for why piecewise."""
    r = pd.Series(ratio) if not isinstance(ratio, pd.Series) else ratio
    below = (r - DURATION_FLOOR_RATIO) / (1.0 - DURATION_FLOOR_RATIO) * DURATION_NEED_SCORE
    above = DURATION_NEED_SCORE + (r - 1.0) / (DURATION_CEILING_RATIO - 1.0) * (
        100.0 - DURATION_NEED_SCORE)
    return below.where(r < 1.0, above).clip(0, 100)


def sleep_debt_and_need(daily: pd.DataFrame) -> pd.DataFrame:
    """Resolve sleep need, debt, and tonight's recommended sleep.

    Two distinct quantities, deliberately kept apart:

    * `sleep_need_h` — your stable physiological baseline. Debt and performance
      are both measured against this.
    * `sleep_recommended_h` — what to aim for tonight: baseline plus repayment
      of current debt plus an allowance for an active day.

    Folding the debt uplift into the need used to *measure* debt creates a
    feedback loop: a chronically short sleeper pins the uplift at its cap, which
    raises need, which enlarges the shortfall, which raises debt again. It is
    also circular — you cannot measure a shortfall against a target that the
    shortfall itself inflated. So the debt accounting uses the baseline only.

    debt_t = decay * debt_{t-1} + max(0, baseline_t - sleep_t)

    Exponential decay rather than a fixed window: recent nights dominate and old
    debt fades, so the number responds when you catch up. Nights with no
    recording hold debt flat rather than decaying it — an unworn ring is not
    evidence of recovery, and decaying through a gap would quietly forgive debt
    that may never have been repaid.
    """
    decay = float(np.exp(-1.0 / DEBT_TAU_DAYS))
    baseline = sleep_need(daily)

    slept = daily["total_sleep_h"].fillna(0) + daily.get(
        "nap_sleep_h", pd.Series(0.0, index=daily.index)
    ).fillna(0)
    has_night = daily["total_sleep_h"].notna()

    steps = daily.get("steps", pd.Series(np.nan, index=daily.index))
    busy_threshold = steps.quantile(ACTIVITY_QUANTILE) if steps.notna().any() else np.inf
    busy_yesterday = (steps.shift(1) >= busy_threshold).fillna(False)

    debts = np.empty(len(daily))
    recommended = np.empty(len(daily))
    debt = 0.0
    base_vals = baseline.to_numpy()
    slept_vals = slept.to_numpy()
    night_vals = has_night.to_numpy()
    busy_vals = busy_yesterday.to_numpy()

    for i in range(len(daily)):
        # Tonight's target reflects the debt carried *into* tonight.
        target = base_vals[i] + min(debt * DEBT_UPLIFT_PER_HOUR, DEBT_UPLIFT_CAP_H)
        if busy_vals[i]:
            target += ACTIVITY_UPLIFT_H
        recommended[i] = float(np.clip(target, NEED_MIN_H,
                                       NEED_MAX_H + DEBT_UPLIFT_CAP_H))

        if night_vals[i]:
            debt = debt * decay + max(0.0, base_vals[i] - slept_vals[i])
        # else: no recording, so hold debt where it is.
        debts[i] = debt

    out = pd.DataFrame({
        "sleep_need_h": baseline.to_numpy(),
        "sleep_recommended_h": recommended,
        "sleep_debt_h": debts,
    }, index=daily.index)
    out["sleep_performance_pct"] = np.where(
        has_night, np.minimum(slept / out["sleep_need_h"] * 100.0, 100.0), np.nan
    )
    return out


# --- score components -------------------------------------------------------

def component_scores(daily: pd.DataFrame) -> pd.DataFrame:
    """Score each component 0-100 relative to personal baseline."""
    out = pd.DataFrame(index=daily.index)

    for metric in SCORE_COMPONENTS:
        key = metric.key
        z_col = f"{key}_z"

        if key == "timing":
            # Deviation from the trailing 7-day median bedtime (min 3 recorded
            # nights). A short window deliberately, so this measures "was
            # tonight like my recent nights" rather than restating SRI, which
            # covers long-run regularity.
            bed = daily["bedtime"]
            median = bed.rolling(TIMING_WINDOW_NIGHTS, min_periods=3).median().shift(1)
            deviation = (bed - median).abs()
            # A clock change makes the wall-clock comparison meaningless.
            deviation = deviation.where(~daily.get(
                "dst_night", pd.Series(False, index=daily.index)
            ).fillna(False))
            raw = 100.0 - metrics.percentile_rank(deviation)

        elif key == "total_sleep_h":
            # Graded against need rather than a percentile: falling short of what
            # your body needed is bad in absolute terms, however typical for you.
            raw = duration_score(daily["total_sleep_h"] / daily["sleep_need_h"])

        elif z_col in daily.columns:
            # Drift-prone signals: compare against the recent seasonal baseline.
            pct = _z_to_percentile(daily[z_col])
            raw = pct if metric.higher_is_better else 100.0 - pct

        elif key in daily.columns:
            pct = metrics.percentile_rank(daily[key])
            raw = pct if metric.higher_is_better else 100.0 - pct

        else:
            raw = pd.Series(np.nan, index=daily.index)

        out[f"c_{key}"] = raw.clip(0, 100)

    return out


def sleep_score(components: pd.DataFrame) -> pd.Series:
    """Weighted mean of available components, renormalised for missing ones.

    Renormalising rather than treating a missing component as zero means one
    absent measurement lowers confidence, not the score itself.
    """
    total = pd.Series(0.0, index=components.index)
    weight = pd.Series(0.0, index=components.index)
    for metric in SCORE_COMPONENTS:
        col = f"c_{metric.key}"
        if col not in components.columns:
            continue
        values = components[col]
        present = values.notna()
        total += values.fillna(0) * metric.weight * present
        weight += metric.weight * present
    # Require at least half the weight before publishing a score.
    return (total / weight).where(weight >= TOTAL_SCORE_WEIGHT * 0.5)


def readiness(daily: pd.DataFrame, score: pd.Series) -> pd.Series:
    """Next-day readiness: how recovered the body looks this morning.

    Each input is first turned into a 0-100 scale where higher always means
    better (so a low resting heart rate scores high), then averaged by weight
    over whatever inputs are present.
    """
    weighted = pd.Series(0.0, index=daily.index)
    weight_sum = pd.Series(0.0, index=daily.index)

    for col, w, higher_is_better in READINESS_PARTS:
        if col == "__score__":
            good = score
        elif col in daily.columns:
            pct = _z_to_percentile(daily[col])
            good = pct if higher_is_better else 100.0 - pct
        else:
            continue
        present = good.notna()
        weighted += good.fillna(0) * w * present
        weight_sum += w * present

    return (weighted / weight_sum.where(weight_sum > 0)).clip(0, 100)
