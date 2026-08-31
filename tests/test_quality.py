"""Stage 1.5 tests — waketime encoding fix, clamping, reliable start, anomalies."""

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import quality, transform  # noqa: E402


def _night(day, start, end, **kw):
    base = {
        "day": day, "type": "long_sleep",
        "bedtime_start": start, "bedtime_end": end,
        "total_sleep_duration": 7 * 3600, "time_in_bed": 8 * 3600,
        "deep_sleep_duration": 3600, "rem_sleep_duration": 5400,
        "light_sleep_duration": 4 * 3600, "latency": 900,
        "efficiency": 92, "restless_periods": 14, "lowest_heart_rate": 48,
        "average_heart_rate": 55, "average_hrv": 61, "average_breath": 14.2,
    }
    base.update(kw)
    return base


# --- Finding 1: the waketime encoding bug -----------------------------------

def test_late_sleeper_span_is_positive():
    """3:00am -> 12:39pm previously produced a -24h span. It must be ~9.65h."""
    df = transform.sleep_sessions_to_frame([
        _night("2025-04-04", "2025-04-04T03:00:00-05:00",
               "2025-04-04T12:39:00-05:00",
               total_sleep_duration=int(9.65 * 3600),
               time_in_bed=int(9.65 * 3600)),
    ])
    row = df.iloc[0]
    assert row["bedtime"] == 27.0                       # 3am -> 27.0
    span = row["waketime"] - row["bedtime"]
    assert span == pytest.approx(9.65, abs=0.02)
    assert row["waketime"] > row["bedtime"]


def test_ordinary_night_span_unchanged():
    df = transform.sleep_sessions_to_frame([
        _night("2024-01-01", "2024-01-01T23:00:00-05:00",
               "2024-01-02T07:00:00-05:00"),
    ])
    row = df.iloc[0]
    assert row["bedtime"] == 23.0
    assert row["waketime"] == 31.0                      # 7am next day
    assert row["waketime"] - row["bedtime"] == pytest.approx(8.0)


def test_waketime_always_after_bedtime_across_patterns():
    recs = [
        _night("2024-01-01", "2024-01-01T21:30:00Z", "2024-01-02T05:00:00Z"),
        _night("2024-01-02", "2024-01-03T01:15:00Z", "2024-01-03T09:45:00Z"),
        _night("2024-01-03", "2024-01-04T04:00:00Z", "2024-01-04T13:30:00Z"),
    ]
    df = transform.sleep_sessions_to_frame(recs)
    assert (df["waketime"] > df["bedtime"]).all()


# --- clamping ---------------------------------------------------------------

def test_clamp_nulls_only_the_bad_value():
    df = pd.DataFrame({
        "day": [dt.date(2024, 1, 1), dt.date(2024, 1, 2)],
        "temp_deviation": [5.28, 0.3],      # first is physiologically impossible
        "hrv": [61.0, 55.0],
        "total_sleep_h": [7.0, 7.5],
    })
    out, dropped = quality.clamp_implausible(df)
    assert dropped == {"temp_deviation": 1}
    assert pd.isna(out.loc[0, "temp_deviation"])
    # The rest of that night survives — we drop values, not nights.
    assert out.loc[0, "hrv"] == 61.0
    assert out.loc[0, "total_sleep_h"] == 7.0


def test_clamp_leaves_clean_data_alone():
    df = pd.DataFrame({"day": [dt.date(2024, 1, 1)], "hrv": [60.0],
                       "hr_low": [48.0], "efficiency": [90.0]})
    out, dropped = quality.clamp_implausible(df)
    assert dropped == {}
    pd.testing.assert_frame_equal(out, df)


# --- reliable start detection -----------------------------------------------

def test_detect_reliable_start_finds_bad_early_era():
    """Mirrors the real steps problem: near-zero until a switchover date."""
    days = pd.date_range("2019-01-01", periods=600).date
    steps = np.concatenate([
        np.random.default_rng(0).integers(0, 300, 300),      # unusable era
        np.random.default_rng(1).integers(8000, 14000, 300),  # real data
    ])
    df = pd.DataFrame({"day": days, "steps": steps.astype(float)})
    start = quality.detect_reliable_start(df, "steps")
    assert start is not None
    # Should land near the switchover, not at the very beginning.
    assert dt.date(2019, 10, 1) <= start <= dt.date(2020, 2, 1)


def test_detect_reliable_start_returns_none_for_clean_series():
    days = pd.date_range("2022-01-01", periods=400).date
    steps = np.random.default_rng(2).integers(8000, 14000, 400).astype(float)
    df = pd.DataFrame({"day": days, "steps": steps})
    assert quality.detect_reliable_start(df, "steps") is None


def test_apply_reliable_starts_nulls_early_values_only():
    days = pd.date_range("2019-01-01", periods=600).date
    steps = np.concatenate([np.full(300, 100.0), np.full(300, 11000.0)])
    df = pd.DataFrame({"day": days, "steps": steps, "hrv": 55.0})
    out, starts = quality.apply_reliable_starts(df, ["steps"])
    assert "steps" in starts
    assert out.loc[out["day"] < starts["steps"], "steps"].isna().all()
    assert out.loc[out["day"] >= starts["steps"], "steps"].notna().all()
    assert out["hrv"].notna().all()          # other metrics untouched


# --- anomaly detection ------------------------------------------------------

def _frame(rows):
    base = {"bedtime": 23.0, "waketime": 31.0, "total_sleep_h": 7.0,
            "time_in_bed_h": 8.0, "efficiency": 90.0, "hrv": 55.0,
            "hr_low": 50.0, "temp_deviation": 0.0, "steps": 10000.0}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_dst_night_flagged_benign():
    rows = [{"day": dt.date(2024, 10, 20 + i)} for i in range(6)]
    rows[3].update({"time_in_bed_h": 9.0})     # span 8h vs 9h in bed = -1h
    out = quality.find_anomalies(_frame(rows))
    dst = out[out["day"] == dt.date(2024, 10, 23)]
    assert len(dst) == 1
    assert bool(dst.iloc[0]["benign"]) is True
    assert "DST" in dst.iloc[0]["reasons"]


def test_short_sleep_and_low_efficiency_flagged_for_review():
    rows = [{"day": dt.date(2024, 3, 1 + i)} for i in range(5)]
    rows[2].update({"total_sleep_h": 3.2, "efficiency": 55.0, "time_in_bed_h": 5.8,
                    "waketime": 28.8})
    out = quality.find_anomalies(_frame(rows))
    flagged = out[out["day"] == dt.date(2024, 3, 3)]
    assert len(flagged) == 1
    assert bool(flagged.iloc[0]["benign"]) is False
    assert "very short sleep" in flagged.iloc[0]["reasons"]
    assert "low efficiency" in flagged.iloc[0]["reasons"]


def test_bedtime_jump_flagged():
    rows = [{"day": dt.date(2024, 5, 1 + i)} for i in range(10)]
    rows[7].update({"bedtime": 18.0, "waketime": 26.0})   # 5h earlier than usual
    out = quality.find_anomalies(_frame(rows))
    assert dt.date(2024, 5, 8) in set(out["day"])


def test_clean_history_produces_no_anomalies():
    rows = [{"day": dt.date(2024, 6, 1 + i)} for i in range(20)]
    out = quality.find_anomalies(_frame(rows))
    assert out.empty
