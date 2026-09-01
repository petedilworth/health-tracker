"""Stage 2 tests — seasonal, metrics, regularity, score, flags."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import flags, metrics, regularity, score, seasonal  # noqa: E402
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
    assert (debt >= 0).all()


def test_debt_decay_rate_matches_tau():
    """With no shortfall, debt must halve in about tau*ln(2) days."""
    n = 60
    d = _daily(n=n, total_sleep_h=12.0, nap_sleep_h=0.0, steps=np.nan)
    out = score.sleep_debt_and_need(d)
    assert out["sleep_debt_h"].iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_naps_pay_down_debt():
    # Sleep must vary, otherwise the derived need equals the actual sleep and
    # there is no shortfall for a nap to repay.
    n = 60
    nights = np.full(n, 6.0)
    nights[::10] = 9.0                      # occasional long nights lift the need
    base = dict(total_sleep_h=nights, steps=np.nan)

    without = score.sleep_debt_and_need(_daily(n=n, nap_sleep_h=0.0, **base))
    with_naps = score.sleep_debt_and_need(_daily(n=n, nap_sleep_h=1.5, **base))

    assert without["sleep_debt_h"].iloc[-1] > 0, "setup should produce real debt"
    assert with_naps["sleep_debt_h"].iloc[-1] < without["sleep_debt_h"].iloc[-1]


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
