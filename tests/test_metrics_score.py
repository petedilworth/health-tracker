"""Stage 2 tests — seasonal, metrics, regularity, score, flags."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import config, flags, metrics, regularity, score, seasonal  # noqa: E402
from sleep.schema import SCORE_COMPONENTS  # noqa: E402


def _daily(n=400, start="2023-01-01", **cols):
    idx = pd.date_range(start, periods=n, freq="D", name="day")
    df = pd.DataFrame(index=idx)
    for k, v in cols.items():
        df[k] = v
    return df


# --- metrics: daily reindexing and the coverage rule ------------------------

def test_to_daily_inserts_missing_days():
    df = pd.DataFrame({"day": ["2024-01-01", "2024-01-05"], "x": [1.0, 2.0]})
    out = metrics.to_daily(df)
    assert len(out) == 5                       # gap filled with empty rows
    assert out["x"].isna().sum() == 3


def test_rolling_mean_respects_calendar_days_not_rows():
    """A 7-day window must mean 7 calendar days even when nights are missing."""
    idx = pd.date_range("2024-01-01", periods=14, freq="D")
    s = pd.Series([10.0] * 14, index=idx)
    s.iloc[3:10] = np.nan                       # a 7-night gap
    out = metrics.rolling_mean(s, 7, min_coverage=0.7)
    # Mid-gap the window is mostly empty, so no average should be published.
    assert pd.isna(out.iloc[8])


def test_rolling_mean_suppressed_below_coverage():
    idx = pd.date_range("2024-01-01", periods=30, freq="D")
    s = pd.Series(np.nan, index=idx)
    s.iloc[:2] = 8.0                            # only 2 of 30 nights present
    out = metrics.rolling_mean(s, 30, min_coverage=0.7)
    assert out.isna().all(), "a 30-day mean from 2 nights must not be published"


def test_rolling_mean_published_with_good_coverage():
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    s = pd.Series([8.0] * 10, index=idx)
    out = metrics.rolling_mean(s, 7, min_coverage=0.7)
    assert out.iloc[-1] == pytest.approx(8.0)


def test_percentile_rank_spans_range():
    s = pd.Series(range(100), dtype=float)
    p = metrics.percentile_rank(s)
    assert p.min() == pytest.approx(1.0)
    assert p.max() == pytest.approx(100.0)


def test_aggregate_periods():
    d = _daily(n=365, x=1.0)
    weekly = metrics.aggregate(d, "x", "weekly")
    annual = metrics.aggregate(d, "x", "annual")
    assert len(weekly) > 50 and len(annual) == 1
    assert (weekly["value"] == 1.0).all()


def test_top_bottom_respects_direction():
    d = _daily(n=5, x=[1.0, 2.0, 3.0, 4.0, 5.0])
    high_good = metrics.top_bottom(d, "x", n=2, higher_is_better=True)
    low_good = metrics.top_bottom(d, "x", n=2, higher_is_better=False)
    assert list(high_good["top"]["value"]) == [5.0, 4.0]
    # For a metric like resting HR, "best" means lowest.
    assert list(low_good["top"]["value"]) == [1.0, 2.0]


# --- seasonal de-trending ---------------------------------------------------

def test_detrend_removes_injected_seasonal_signal():
    idx = pd.date_range("2020-01-01", periods=365 * 4, freq="D", name="day")
    seasonal_wave = 5 * np.sin(2 * np.pi * idx.dayofyear / 365.25)
    df = pd.DataFrame({"hrv": 50 + seasonal_wave}, index=idx)

    raw_spread = df["hrv"].groupby(df.index.month).mean().std()
    adjusted = seasonal.detrend(df, "hrv")
    adj_spread = adjusted.groupby(df.index.month).mean().std()
    assert adj_spread < raw_spread * 0.1, "month-to-month signal should be removed"


def test_rolling_z_flags_a_departure_from_baseline():
    idx = pd.date_range("2023-01-01", periods=200, freq="D", name="day")
    values = np.random.default_rng(0).normal(50, 5, 200)
    values[-1] = 80.0                            # a big spike on the last night
    df = pd.DataFrame({"hrv": values}, index=idx)
    z = seasonal.rolling_z(df, "hrv")
    assert z.iloc[-1] > 3


def test_rolling_z_does_not_use_tonight_in_its_own_baseline():
    idx = pd.date_range("2023-01-01", periods=120, freq="D", name="day")
    df = pd.DataFrame({"hrv": [50.0] * 119 + [90.0]}, index=idx)
    z = seasonal.rolling_z(df, "hrv")
    # If tonight leaked into its own mean, the z-score would be muted.
    assert pd.isna(z.iloc[-1]) or z.iloc[-1] > 5


# --- SRI --------------------------------------------------------------------

def test_sri_high_for_regular_sleeper():
    d = _daily(n=90, bedtime=23.0, waketime=31.0)
    sri = regularity.sleep_regularity_index(d).dropna()
    assert sri.iloc[-1] > 95


def test_sri_lower_for_chaotic_sleeper():
    rng = np.random.default_rng(3)
    n = 90
    bed = 20.0 + rng.uniform(0, 8, n)             # bedtime anywhere 8pm-4am
    d = _daily(n=n, bedtime=bed, waketime=bed + 7.0)
    sri = regularity.sleep_regularity_index(d).dropna()
    assert sri.iloc[-1] < 75


def test_sri_ranks_regular_above_chaotic():
    regular = _daily(n=90, bedtime=23.0, waketime=31.0)
    rng = np.random.default_rng(5)
    bed = 20.0 + rng.uniform(0, 8, 90)
    chaotic = _daily(n=90, bedtime=bed, waketime=bed + 7.0)
    assert (regularity.sleep_regularity_index(regular).dropna().iloc[-1]
            > regularity.sleep_regularity_index(chaotic).dropna().iloc[-1])


def test_sleep_matrix_marks_expected_minutes():
    d = _daily(n=2, bedtime=23.0, waketime=31.0)   # 11pm -> 7am
    m = regularity.build_sleep_matrix(d)
    assert m.shape == (2, 1440)
    assert m[0, 23 * 60] and not m[0, 22 * 60]      # asleep at 11pm, awake at 10pm


# --- sleep need and debt ----------------------------------------------------

def test_debt_accumulates_then_decays():
    n = 120
    sleep = np.full(n, 8.0)
    sleep[60:70] = 4.0                              # ten short nights
    d = _daily(n=n, total_sleep_h=sleep, nap_sleep_h=0.0, steps=np.nan)
    out = score.sleep_debt_and_need(d)
    debt = out["sleep_debt_h"]
    peak = debt.iloc[69]
    assert peak > debt.iloc[59], "debt should build during the short stretch"
    assert debt.iloc[100] < peak * 0.5, "and decay once sleep recovers"


def test_surplus_sleep_repays_debt_and_can_go_negative():
    """A night above need must repay debt 1:1, with no floor at zero.

    An earlier version floored the nightly term at zero, so sleeping nine hours
    and sleeping exactly to need repaid identical amounts — the surplus was
    simply discarded.
    """
    n = 200
    rng = np.random.default_rng(3)
    sleep = rng.normal(6.0, 0.8, n).clip(4.0, 8.0)
    lean = score.sleep_debt_and_need(
        _daily(n=n, total_sleep_h=sleep, nap_sleep_h=0.0, steps=np.nan))

    generous = sleep.copy()
    generous[150:] += 3.0                           # a long, genuine surplus
    rich = score.sleep_debt_and_need(
        _daily(n=n, total_sleep_h=generous, nap_sleep_h=0.0, steps=np.nan))

    assert lean["sleep_debt_h"].iloc[-1] > 0, "setup should build real debt"
    assert rich["sleep_debt_h"].iloc[-1] < 0, "a sustained surplus banks sleep"


def test_debt_decay_rate_matches_tau():
    """With no shortfall, standing debt decays at exactly exp(-1/tau) a night."""
    n = 160
    sleep = np.full(n, 7.0)
    sleep[40:50] = 4.0                              # build some debt to decay
    d = _daily(n=n, total_sleep_h=sleep, nap_sleep_h=0.0, steps=np.nan)
    debt = score.sleep_debt_and_need(d)["sleep_debt_h"]

    # Sleep sits exactly on need (the P75 of a mostly-constant series) from
    # night 50 on, so every later change is pure decay.
    peak = debt.iloc[49]
    assert peak > 0
    assert debt.iloc[59] / peak == pytest.approx(np.exp(-10 / score.DEBT_TAU_DAYS))
    # tau is derived from the half-life, which is the tunable a human reasons
    # about. Asserting the relation, not the value, so retuning never breaks it.
    assert (score.DEBT_TAU_DAYS * np.log(2)
            == pytest.approx(score.DEBT_HALF_LIFE_DAYS))
    halved = debt.iloc[49 + round(score.DEBT_HALF_LIFE_DAYS)]
    assert halved == pytest.approx(peak * 0.5, rel=0.1)


def test_naps_pay_down_debt():
    # Sleep must vary smoothly: with a spiky distribution the need percentile
    # can land exactly on the modal value, leaving no shortfall to repay.
    n = 120
    nights = np.random.default_rng(21).normal(6.0, 0.9, n).clip(4.0, 9.0)
    base = dict(total_sleep_h=nights, steps=np.nan)

    without = score.sleep_debt_and_need(_daily(n=n, nap_sleep_h=0.0, **base))
    with_naps = score.sleep_debt_and_need(_daily(n=n, nap_sleep_h=1.5, **base))

    assert without["sleep_debt_h"].iloc[-1] > 0, "setup should produce real debt"
    assert with_naps["sleep_debt_h"].iloc[-1] < without["sleep_debt_h"].iloc[-1]


def test_debt_does_not_inflate_its_own_target():
    """Regression: a chronically short sleeper must not pin the need upward.

    Debt used to be measured against a need that the debt itself raised, which
    is circular and pinned the uplift at its cap forever.
    """
    # A smooth distribution, so the rolling P90 doesn't sit on a discontinuity.
    n = 300
    nights = np.random.default_rng(7).normal(6.0, 0.9, n).clip(4.0, 9.0)
    d = _daily(n=n, total_sleep_h=nights, nap_sleep_h=0.0, steps=np.nan)
    out = score.sleep_debt_and_need(d)

    need, rec = out["sleep_need_h"], out["sleep_recommended_h"]
    # The debt level scales with the half-life, so guard against half the
    # theoretical steady state rather than a number tuned to one setting.
    weight = 1.0 / (1.0 - np.exp(-1 / score.DEBT_TAU_DAYS))
    shortfall = float((need - pd.Series(nights, index=d.index)).mean())
    assert out["sleep_debt_h"].iloc[-1] > 0.5 * shortfall * weight, \
        "setup should build real debt"
    # The measurement baseline must not drift upward as debt accumulates.
    assert need.iloc[-1] == pytest.approx(need.iloc[150], abs=0.3)
    # Tonight's recommendation may exceed it — that's the actionable number.
    assert rec.iloc[-1] > need.iloc[-1]


def test_need_falls_back_to_the_quantile_without_time_in_bed():
    """No time-in-bed column, so the opportunity estimator can't run.

    Reads NEED_QUANTILE rather than hardcoding it, so recalibrating the fallback
    doesn't break the test.
    """
    rng = np.random.default_rng(11)
    nights = rng.normal(6.7, 0.95, 400).clip(4, 10)
    d = _daily(n=400, total_sleep_h=nights, nap_sleep_h=0.0, steps=np.nan)
    out = score.sleep_debt_and_need(d)
    expected = pd.Series(nights).quantile(score.NEED_QUANTILE)
    assert out["sleep_need_h"].median() == pytest.approx(expected, abs=0.05)


def test_need_falls_back_when_too_few_generous_nights():
    """A handful of long nights is not enough to estimate a requirement from."""
    n = 400
    rng = np.random.default_rng(5)
    nights = rng.normal(6.5, 0.8, n).clip(4, 9)
    tib = np.full(n, 7.0)
    tib[:score.NEED_MIN_OPPORTUNITY_NIGHTS - 1] = 9.0    # one short of enough
    d = _daily(n=n, total_sleep_h=nights, time_in_bed_h=tib,
               nap_sleep_h=0.0, steps=np.nan)
    need = score.sleep_debt_and_need(d)["sleep_need_h"]
    expected = pd.Series(nights).quantile(score.NEED_QUANTILE)
    assert need.iloc[-1] == pytest.approx(expected, abs=0.05)


def test_need_is_the_median_sleep_on_generous_nights():
    """The estimator conditions on opportunity, not on where a percentile lands."""
    n = 400
    rng = np.random.default_rng(17)
    generous = rng.normal(7.8, 0.4, n // 2).clip(6, 10)   # room to sleep
    cramped = rng.normal(6.0, 0.4, n - n // 2).clip(4, 8)  # no room
    nights = np.empty(n)
    nights[0::2], nights[1::2] = generous, cramped
    tib = np.empty(n)
    tib[0::2] = score.NEED_OPPORTUNITY_TIB_H + 0.7
    tib[1::2] = score.NEED_OPPORTUNITY_TIB_H - 0.7
    d = _daily(n=n, total_sleep_h=nights, time_in_bed_h=tib,
               nap_sleep_h=0.0, steps=np.nan)

    need = score.sleep_debt_and_need(d)["sleep_need_h"]
    assert need.iloc[-1] == pytest.approx(float(np.median(generous)), abs=0.05)
    # The cramped nights must not drag it down, as any plain quantile would.
    assert need.iloc[-1] > pd.Series(nights).quantile(score.NEED_QUANTILE)


def test_need_holds_when_the_schedule_tightens():
    """The 2026 property, and the reason for conditioning on opportunity.

    Real case: median sleep fell to the worst in the record while sleep on
    nights with 8h in bed rose to the best. A percentile anchor lowers the bar
    exactly when it should hold.
    """
    n, tail = 500, 150
    rng = np.random.default_rng(23)
    nights = rng.normal(7.2, 0.5, n).clip(5, 10)
    tib = np.full(n, score.NEED_OPPORTUNITY_TIB_H + 0.5)
    # A late stretch where he simply stops giving himself the time.
    nights[-tail:] = rng.normal(5.8, 0.4, tail).clip(4, 7)
    tib[-tail:] = score.NEED_OPPORTUNITY_TIB_H - 1.5

    full = _daily(n=n, total_sleep_h=nights, time_in_bed_h=tib,
                  nap_sleep_h=0.0, steps=np.nan)
    before = _daily(n=n - tail, total_sleep_h=nights[:-tail],
                    time_in_bed_h=tib[:-tail], nap_sleep_h=0.0, steps=np.nan)

    # The cramped stretch contributes no qualifying nights, so it moves nothing.
    assert (score.sleep_need(full).iloc[-1]
            == pytest.approx(float(score.sleep_need(before).iloc[-1]), abs=1e-9))
    # A quantile of all sleep, the old behaviour, does follow the schedule down.
    assert (pd.Series(nights).quantile(score.NEED_QUANTILE)
            < pd.Series(nights[:-tail]).quantile(score.NEED_QUANTILE) - 0.1)


def test_need_is_flat_and_does_not_follow_a_declining_stretch():
    """A trailing window would let six months of poor sleep lower the bar."""
    good = np.full(300, 8.0)
    bad = np.full(200, 5.5)          # a long decline at the end
    d = _daily(n=500, total_sleep_h=np.concatenate([good, bad]),
               nap_sleep_h=0.0, steps=np.nan)
    need = score.sleep_debt_and_need(d)["sleep_need_h"]
    assert need.nunique() == 1, "need must be one stable number, not a moving target"
    # It must still reflect the good era rather than collapsing to the bad one.
    assert need.iloc[-1] > 5.5


# --- sleep opportunity ------------------------------------------------------

def _opp(n=120, tib=8.0, start="2023-01-02"):
    """A weekday-anchored frame; 2023-01-02 is a Monday."""
    return _daily(n=n, start=start, time_in_bed_h=tib, waketime=31.0)


def test_opportunity_gap_is_time_in_bed_against_the_target():
    out = score.sleep_opportunity(_opp(n=10, tib=7.25))
    target = config.TARGET_TIB_H
    assert (out["opportunity_target_h"] == target).all()
    assert out["opportunity_gap_h"].iloc[-1] == pytest.approx(7.25 - target)


def test_opportunity_debt_accumulates_then_repays():
    n = 200
    tib = np.full(n, config.TARGET_TIB_H)
    tib[40:60] = config.TARGET_TIB_H - 2.0        # twenty cramped nights
    debt = score.sleep_opportunity(_opp(n=n, tib=tib))["opportunity_debt_h"]

    assert debt.iloc[59] > debt.iloc[39], "cramped nights build debt"
    assert debt.iloc[39] == pytest.approx(0.0, abs=1e-9), "on target means no debt"
    # Sitting exactly on target afterwards, everything later is pure decay.
    assert debt.iloc[100] < debt.iloc[59] * 0.2


def test_opportunity_debt_is_symmetric_and_unfloored():
    """Extra time in bed repays 1:1, and a real surplus banks. Same rule as
    sleep debt, so the two metrics read alike."""
    n = 120
    debt = score.sleep_opportunity(
        _opp(n=n, tib=config.TARGET_TIB_H + 1.0))["opportunity_debt_h"]
    assert debt.iloc[-1] < 0
    assert debt.iloc[-1] == pytest.approx(
        -1.0 / (1 - np.exp(-1 / score.DEBT_TAU_DAYS)), abs=0.05)


def test_opportunity_debt_decay_matches_tau():
    n = 160
    tib = np.full(n, config.TARGET_TIB_H)
    tib[40:50] = config.TARGET_TIB_H - 3.0
    debt = score.sleep_opportunity(_opp(n=n, tib=tib))["opportunity_debt_h"]
    peak = debt.iloc[49]
    assert peak > 0
    assert debt.iloc[59] / peak == pytest.approx(np.exp(-10 / score.DEBT_TAU_DAYS))


def test_opportunity_debt_holds_flat_across_a_gap():
    """An unworn ring is not evidence you went to bed early."""
    n = 80
    tib = np.full(n, config.TARGET_TIB_H - 1.5)
    tib[40:50] = np.nan
    debt = score.sleep_opportunity(_opp(n=n, tib=tib))["opportunity_debt_h"]
    assert debt.iloc[40:50].nunique() == 1
    assert debt.iloc[49] == pytest.approx(debt.iloc[39])


def test_bedtime_target_is_the_configured_wake_minus_the_target():
    """06:15 up, 8h in bed, so lights out at 22:15 on a weekday."""
    out = score.sleep_opportunity(_opp(n=7))
    monday = out.index[0]
    assert monday.dayofweek == 0
    expected = config.TARGET_WAKE_WEEKDAY_H + 24.0 - config.TARGET_TIB_H
    assert out["bedtime_target"].iloc[0] == pytest.approx(expected)
    assert expected % 24 == pytest.approx(22.25)      # 22:15
    # Weekends fall back to the observed lie-in rather than inventing a schedule.
    saturday = out.index.dayofweek == 5
    assert out.loc[saturday, "bedtime_target"].iloc[0] == pytest.approx(31.0 - 8.0)


def test_nights_to_clear_matches_the_closed_form():
    from sleep import site
    decay = np.exp(-1 / score.DEBT_TAU_DAYS)
    assert site._nights_to_clear(None) is None
    assert site._nights_to_clear(0.4) is None
    assert site._nights_to_clear(1.0) is None, "already under the threshold"
    for debt in (2.0, 5.91, 12.0):
        assert site._nights_to_clear(debt) == int(
            np.ceil(np.log(1.0 / debt) / np.log(decay)))
    # A shorter half-life must always clear faster.
    assert site._nights_to_clear(6.0) < site._nights_to_clear(6.0, threshold=0.5)


def test_opportunity_yield_recovers_a_known_slope():
    n = 300
    rng = np.random.default_rng(9)
    tib = rng.normal(8.0, 0.8, n).clip(6, 11)
    d = _daily(n=n, time_in_bed_h=tib, total_sleep_h=0.8 * tib + 0.4)
    assert score.opportunity_yield(d) == pytest.approx(48.0, abs=0.5)


# --- duration curve ---------------------------------------------------------

def test_duration_curve_breakpoints():
    s = score.duration_score(pd.Series([0.50, 0.65, 1.00, 1.20, 1.50]))
    assert s.iloc[0] == 0                                  # far below floor
    assert s.iloc[1] == pytest.approx(0, abs=1e-9)         # floor
    assert s.iloc[2] == pytest.approx(score.DURATION_NEED_SCORE)   # meeting need
    assert s.iloc[3] == pytest.approx(100)                 # ceiling
    assert s.iloc[4] == 100                                # clipped beyond


def test_duration_curve_is_monotonic():
    r = pd.Series(np.linspace(0.4, 1.5, 200))
    s = score.duration_score(r)
    assert (s.diff().dropna() >= -1e-9).all()


def test_duration_curve_no_longer_piles_up_at_the_ceiling():
    """The old linear-capped curve scored a quarter of nights exactly 100."""
    rng = np.random.default_rng(3)
    # Ratios shaped like real data: median ~0.92, a quarter at or above 1.0.
    ratios = pd.Series(rng.normal(0.92, 0.12, 2000)).clip(0.4, 1.6)
    s = score.duration_score(ratios)
    assert (s >= 99.5).mean() < 0.05
    assert s.std() > 20


def test_debt_holds_flat_across_a_gap():
    """An unworn ring is not evidence of recovery, so debt must not decay."""
    n = 60
    nights = np.full(n, 5.5)
    nights[::10] = 8.0
    nights[40:50] = np.nan             # a ten-night gap
    d = _daily(n=n, total_sleep_h=nights, nap_sleep_h=0.0, steps=np.nan)
    debt = score.sleep_debt_and_need(d)["sleep_debt_h"]
    assert debt.iloc[40:50].nunique() == 1, "debt should be frozen through the gap"
    assert debt.iloc[49] == pytest.approx(debt.iloc[39])


def test_performance_measured_against_baseline_not_recommendation():
    n = 200
    nights = np.full(n, 6.0)
    nights[::10] = 8.0
    d = _daily(n=n, total_sleep_h=nights, nap_sleep_h=0.0, steps=np.nan)
    out = score.sleep_debt_and_need(d)
    row = out.iloc[-1]
    expected = min(nights[-1] / row["sleep_need_h"] * 100, 100)
    assert row["sleep_performance_pct"] == pytest.approx(expected, abs=0.01)


def test_sleep_need_stays_in_bounds():
    d = _daily(n=300, total_sleep_h=np.random.default_rng(1).normal(7, 1.5, 300),
               nap_sleep_h=0.0, steps=np.nan)
    need = score.sleep_debt_and_need(d)["sleep_need_h"]
    assert need.between(score.NEED_MIN_H,
                        score.NEED_MAX_H + score.DEBT_UPLIFT_CAP_H).all()


# --- scoring ----------------------------------------------------------------

def test_score_renormalises_for_missing_components():
    """A missing component should not drag the score toward zero."""
    idx = pd.date_range("2024-01-01", periods=2, freq="D", name="day")
    comps = pd.DataFrame(index=idx)
    for m in SCORE_COMPONENTS:
        comps[f"c_{m.key}"] = 80.0
    comps.loc[idx[1], "c_rem_h"] = np.nan          # one component absent
    out = score.sleep_score(comps)
    assert out.iloc[0] == pytest.approx(80.0)
    assert out.iloc[1] == pytest.approx(80.0)      # still 80, not diluted


def test_score_withheld_when_too_little_data():
    idx = pd.date_range("2024-01-01", periods=1, freq="D", name="day")
    comps = pd.DataFrame(index=idx)
    for m in SCORE_COMPONENTS:
        comps[f"c_{m.key}"] = np.nan
    comps["c_hrv"] = 90.0                          # only 1.0 of 7.5 weight
    assert score.sleep_score(comps).isna().all()


def test_score_is_weighted_not_a_plain_mean():
    idx = pd.date_range("2024-01-01", periods=1, freq="D", name="day")
    comps = pd.DataFrame(index=idx)
    for m in SCORE_COMPONENTS:
        comps[f"c_{m.key}"] = 50.0
    comps["c_hrv"] = 100.0        # weight 1.0
    comps["c_rem_h"] = 100.0      # weight 0.25 — should move the score less
    only_hrv = comps.copy(); only_hrv["c_rem_h"] = 50.0
    only_rem = comps.copy(); only_rem["c_hrv"] = 50.0
    assert score.sleep_score(only_hrv).iloc[0] > score.sleep_score(only_rem).iloc[0]


def test_z_to_percentile_is_calibrated():
    z = pd.Series([-1.96, 0.0, 1.96])
    p = score._z_to_percentile(z)
    assert p.iloc[0] == pytest.approx(2.5, abs=0.2)
    assert p.iloc[1] == pytest.approx(50.0, abs=0.1)
    assert p.iloc[2] == pytest.approx(97.5, abs=0.2)


# --- flags ------------------------------------------------------------------

def test_flag_fires_on_elevated_temperature():
    d = _daily(n=3, temp_deviation=[0.0, 0.1, 0.9])
    d["temp_deviation_z"] = [0.1, 0.3, 3.0]
    d["breaths_per_min"] = 13.0
    d["breaths_per_min_z"] = 0.0
    out = flags.health_flags(d)
    assert not out["flag_raised"].iloc[0]
    assert out["flag_raised"].iloc[2]
    assert "Body temperature" in out["flag_detail"].iloc[2]


def test_flag_silent_on_normal_nights():
    d = _daily(n=5, temp_deviation=0.0, breaths_per_min=13.0)
    d["temp_deviation_z"] = 0.2
    d["breaths_per_min_z"] = -0.1
    out = flags.health_flags(d)
    assert not out["flag_raised"].any()
    assert (out["flag_detail"] == "").all()


def test_top_bottom_with_a_target_ranks_best_by_closeness():
    """Bedtime: earliest-is-best crowned three 7pm nights. Best must be nearest
    the target; worst stays the monotonic extreme."""
    d = _daily(n=6, x=[18.5, 21.0, 22.0, 22.5, 23.0, 28.0])
    out = metrics.top_bottom(d, "x", n=3, higher_is_better=False, target=22.25)
    assert sorted(out["top"]["value"]) == [22.0, 22.5, 23.0]
    assert list(out["bottom"]["value"]) == [28.0, 23.0, 22.5]
    # Without a target the old behaviour is unchanged.
    plain = metrics.top_bottom(d, "x", n=3, higher_is_better=False)
    assert list(plain["top"]["value"]) == [18.5, 21.0, 22.0]
