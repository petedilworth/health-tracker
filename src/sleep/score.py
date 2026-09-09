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

import math

import numpy as np
import pandas as pd

from . import config, metrics
from .schema import SCORE_COMPONENTS, TOTAL_SCORE_WEIGHT

# --- sleep need -------------------------------------------------------------
# Need is estimated from nights when you had ADEQUATE OPPORTUNITY to sleep, not
# from a percentile of all recorded sleep.
#
# Why (established 2026-09 against 2,484 nights): sleep here is opportunity
# limited across the entire observed range and never saturates. Each extra hour
# in bed buys ~48 minutes of sleep, still ~37 minutes an hour above 8.5h in bed,
# and efficiency barely moves (88% at 7h in bed, 84% past 10h). Someone sleeping
# at their need shows the opposite: extra opportunity turns into wake. So any
# quantile of recorded sleep measures the SCHEDULE, not the requirement, and
# inherits every restriction in it.
#
# 2026 is the clean test. Median sleep fell to 6.59h, the worst in the record,
# while sleep on nights with >=8h in bed rose to 7.70h, the best in the record.
# A percentile anchor lowers the bar exactly when it should hold; this one held.
#
# The estimator is stable, which is the property that matters. Computed as an
# expanding median at each year end it reads 7.32 / 7.38 / 7.41h for 2020 / 2023
# / 2026, against 7.19 / 7.26 / 7.27h for the old P75.
#
# Honest limit: long nights in bed are exactly where Oura's wake detection is
# weakest (wake specificity 29-52%), so this is biased upward by an unknown
# amount. Hence the conservative 8h threshold rather than 8.5h (7.75h) or 9h
# (8.20h). Revisited annually by the recalibration-review workflow.
NEED_OPPORTUNITY_TIB_H = 8.0
NEED_MIN_OPPORTUNITY_NIGHTS = 60
# Fallback only, for a history with no time-in-bed column or too few qualifying
# nights to estimate from. Kept at the P75 it used to be.
NEED_QUANTILE = 0.75
NEED_MIN_H, NEED_MAX_H = 6.0, 10.0

# --- duration scoring -------------------------------------------------------
# How sleep duration maps to 0-100 against need. Piecewise, because a single
# linear ramp capped at need piles nights up on the ceiling: need is set from
# your better-opportunity nights, so a large share of nights land on or past it
# and a hard cap at 100 scored 26% of all nights identically — the same
# compression that made Oura's score uninformative. Meeting need is a strong
# 85; the last 15 points are reserved for genuinely exceeding it.
DURATION_FLOOR_RATIO = 0.65     # ≤65% of need scores 0
DURATION_NEED_SCORE = 85.0      # hitting need exactly
DURATION_CEILING_RATIO = 1.20   # ≥120% of need scores 100

# --- recommended tonight ----------------------------------------------------
# Uplifts added on top of baseline need to give tonight's target. Kept out of
# the need used to *measure* debt; see sleep_debt_and_need for why.
DEBT_UPLIFT_PER_HOUR = 0.15     # repay this share of standing debt tonight
DEBT_UPLIFT_CAP_H = 1.0
ACTIVITY_UPLIFT_H = 0.25        # after a top-quintile step day
ACTIVITY_QUANTILE = 0.80

# --- debt -------------------------------------------------------------------
# The tunable is the half-life, because that is the part a person can reason
# about; tau is derived from it.
#
# Set 2026-09 from lived experience, NOT from the recovery literature, and the
# distinction matters. One bad night gets absorbed, two or three compound, and a
# few solid nights restore — that is a ~3-day half-life. The previous 9.7 days
# came from recovery studies (Kitamura 2016, Banks 2010) and was too slow to
# reinforce anything: a good week barely moved the number, which defeats the
# point of a tool built to change behaviour.
#
# What this trades away, stated plainly: subjective recovery outpaces objective
# recovery. Van Dongen et al. (2003) found subjective sleepiness plateaus while
# PVT deficits keep accumulating; Belenky et al. (2003) found three recovery
# nights after a week of restriction did not restore performance. So this number
# will read clear before function fully is. The 30-day average on the chart is
# the honest long-run read, and it is actually *better* at a short half-life: it
# correlates 0.91 with the true 30-day shortfall here, against 0.77 at 9.7 days,
# because the slow series was so smoothed that its own 30-day mean lagged.
#
# Checked before changing: the half-life is almost purely a recovery-speed dial.
# Three consecutive nights 2h short peak at 4.81h here against 5.60h at 9.7 days,
# so bad nights still cost what they cost. Only the time to work them off moves,
# from 25 nights to 7. The series stays smooth (lag-1 autocorrelation 0.83), and
# the DEBT_UPLIFT_CAP_H stops being pinned — it bound on 86% of nights at 9.7
# days, and on 1.7% at three.
DEBT_HALF_LIFE_DAYS = 3.0
DEBT_TAU_DAYS = DEBT_HALF_LIFE_DAYS / math.log(2)      # ~4.33
# Sleeping beyond need repays debt 1:1, and debt may go negative — a genuine
# surplus. Sleep banking is real: Rupp & Wesensten (2009) showed a week of
# extended sleep before restriction bought a 2-3 day grace period before
# performance degraded. Note the asymmetry caveat: recovery generally lags
# accumulation in the literature, so 1:1 is the optimistic end of defensible.

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

    The median sleep you achieve on nights when you gave yourself adequate
    opportunity — at least NEED_OPPORTUNITY_TIB_H in bed. One flat number,
    recomputed as history accumulates. See the NEED_* constants for why this
    rather than a percentile of all recorded sleep.

    Conditioning on opportunity is what stops the baseline following behaviour
    down. A trailing window or a plain quantile both track recent nights, so a
    stretch of poor sleep quietly lowers the bar it is judged against; this
    dataset's need had once drifted 7.27h -> 6.95h that way. Conditioning on
    opportunity instead means a tightening schedule removes nights from the
    estimate rather than dragging it lower.

    Falls back to the NEED_QUANTILE of all sleep when there is no time-in-bed
    column, or fewer than NEED_MIN_OPPORTUNITY_NIGHTS qualifying nights.

    Not adjusted for debt or activity — those uplifts live in
    `sleep_recommended_h`. This baseline is what debt accounting and
    performance % grade against.
    """
    total = daily["total_sleep_h"]
    if not total.notna().any():
        return pd.Series(8.0, index=daily.index)

    tib = daily["time_in_bed_h"] if "time_in_bed_h" in daily.columns else None
    if tib is not None:
        generous = total.where(tib >= NEED_OPPORTUNITY_TIB_H)
        if int(generous.notna().sum()) >= NEED_MIN_OPPORTUNITY_NIGHTS:
            baseline = float(np.clip(generous.median(), NEED_MIN_H, NEED_MAX_H))
            return pd.Series(baseline, index=daily.index)

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

    debt_t = decay * debt_{t-1} + (baseline_t - sleep_t)

    Symmetric and unfloored: a night above need repays debt 1:1 and can push it
    negative into a genuine surplus, because sleep banking is real (see the
    DEBT_* constants). An earlier version took max(0, ...) of the shortfall, so
    sleeping nine hours and sleeping exactly to need reduced debt by identical
    amounts — only the decay repaid anything, and the surplus was discarded.

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
        # Tonight's target reflects the debt carried *into* tonight. Clamped at
        # zero: a surplus never licenses sleeping less than baseline need.
        uplift = float(np.clip(debt * DEBT_UPLIFT_PER_HOUR, 0.0, DEBT_UPLIFT_CAP_H))
        target = base_vals[i] + uplift
        if busy_vals[i]:
            target += ACTIVITY_UPLIFT_H
        recommended[i] = float(np.clip(target, NEED_MIN_H,
                                       NEED_MAX_H + DEBT_UPLIFT_CAP_H))

        if night_vals[i]:
            # Symmetric: a surplus repays 1:1 and may carry debt negative.
            debt = debt * decay + (base_vals[i] - slept_vals[i])
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


# --- sleep opportunity ------------------------------------------------------

def opportunity_yield(daily: pd.DataFrame) -> float:
    """Minutes of sleep bought per extra hour in bed, fitted on your history.

    Kept live rather than hardcoded so the site quotes the current number. The
    slope is the whole argument for the opportunity metric: it is ~48 min/h
    here, and it stays high (~37 min/h) even above 8.5h in bed, which is what
    tells you the sleep system has not found its ceiling.
    """
    if "time_in_bed_h" not in daily.columns:
        return float("nan")
    pair = daily[["time_in_bed_h", "total_sleep_h"]].dropna()
    if len(pair) < 30:
        return float("nan")
    slope = float(np.polyfit(pair["time_in_bed_h"], pair["total_sleep_h"], 1)[0])
    return slope * 60.0


def _target_waketime(daily: pd.DataFrame) -> pd.Series:
    """Target waketime per night, as the same +24-shifted decimal hour as data.

    Weekdays use the configured target. Weekends use the configured one if set,
    otherwise your own trailing weekend median, so the number stays honest about
    a lie-in you actually take rather than inventing a schedule for you.
    """
    idx = daily.index
    weekend = pd.Series(idx.dayofweek >= 5, index=idx)

    weekend_target = config.TARGET_WAKE_WEEKEND_H
    if weekend_target is None:
        observed = daily.get("waketime", pd.Series(np.nan, index=idx))
        observed = observed.where(weekend).tail(365)
        weekend_target = (float(observed.median()) if observed.notna().any()
                          else config.TARGET_WAKE_WEEKDAY_H + 24.0)
    elif weekend_target < 12.0:
        weekend_target += 24.0          # after-midnight convention, as waketime

    weekday_target = config.TARGET_WAKE_WEEKDAY_H
    if weekday_target < 12.0:
        weekday_target += 24.0

    return pd.Series(np.where(weekend, weekend_target, weekday_target), index=idx)


def sleep_opportunity(daily: pd.DataFrame) -> pd.DataFrame:
    """Time-in-bed debt against your declared target. The controllable half.

    Sleep debt grades something you do not directly control and that the ring
    measures least well. This grades the opportunity you gave yourself: time in
    bed, derived from bedtime and waketime, which is the high-confidence tier.
    You can act on it tonight without lying awake doing arithmetic.

    opportunity_debt_t = decay * opportunity_debt_{t-1} + (target - time_in_bed_t)

    Same accumulator as sleep debt on purpose, so the two read alike: identical
    DEBT_TAU_DAYS, symmetric so a long night in bed repays 1:1, unfloored so a
    real surplus banks, and flat across nights with no recording.

    The target is declared in config, not derived. Full sleep need would imply
    8.52h in bed at this efficiency, met on 19.5% of nights with a best streak of
    four; the declared 8.0h is met on 39.2% with a best streak of ten. A target
    you clear two nights in five is a target you keep; see config for the note on
    what that leaves on the table.
    """
    idx = daily.index
    target = float(config.TARGET_TIB_H)
    decay = float(np.exp(-1.0 / DEBT_TAU_DAYS))

    tib = daily.get("time_in_bed_h", pd.Series(np.nan, index=idx))
    recorded = tib.notna()

    debts = np.empty(len(daily))
    debt = 0.0
    tib_vals = tib.to_numpy()
    rec_vals = recorded.to_numpy()
    for i in range(len(daily)):
        if rec_vals[i]:
            # Symmetric and unfloored, exactly as sleep debt.
            debt = debt * decay + (target - tib_vals[i])
        # else: no recording, so hold debt where it is.
        debts[i] = debt

    out = pd.DataFrame({
        "opportunity_target_h": np.full(len(daily), target),
        "opportunity_gap_h": (tib - target).to_numpy(),
        "opportunity_debt_h": debts,
    }, index=idx)
    out["bedtime_target"] = (_target_waketime(daily) - target).to_numpy()
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
