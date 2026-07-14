"""Unit tests for the processing + storage logic (no network needed)."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oura import processing, storage  # noqa: E402


def test_bedtime_after_midnight_shift():
    df = processing.sleep_sessions_to_frame([
        {"type": "long_sleep", "day": "2024-01-01",
         "bedtime_start": "2024-01-01T23:30:00-05:00"},
        {"type": "long_sleep", "day": "2024-01-02",
         "bedtime_start": "2024-01-03T01:00:00-05:00"},
    ])
    bt = dict(zip(df["day"].astype(str), df["bedtime"]))
    assert bt["2024-01-01"] == 23.5      # evening stays as-is
    assert bt["2024-01-02"] == 25.0      # 1am -> 25.0, not 1.0


def test_naps_excluded():
    df = processing.sleep_sessions_to_frame([
        {"type": "long_sleep", "day": "2024-01-01",
         "bedtime_start": "2024-01-01T23:00:00-05:00"},
        {"type": "late_nap", "day": "2024-01-01",
         "bedtime_start": "2024-01-01T14:00:00-05:00"},
    ])
    assert len(df) == 1
    assert df["bedtime"].iloc[0] == 23.0


def test_percentiles_and_rolling():
    days = pd.date_range("2024-01-01", periods=40).date
    raw = pd.DataFrame({"day": days,
                        "bedtime": [23.0] * 40,
                        "score": list(range(60, 100))})
    out = processing.add_rolling_and_percentiles(raw)
    # Highest score is the last row -> ~100th percentile daily.
    assert out["score_pct_daily"].iloc[-1] == 100.0
    # 7d avg exists and is <= the daily max.
    assert out["score_7d"].iloc[-1] <= out["score"].iloc[-1]
    assert {"score_7d", "score_30d", "score_pct_7d", "score_pct_30d"} <= set(out.columns)


def test_upsert_is_idempotent():
    a = pd.DataFrame({"day": ["2024-01-01", "2024-01-02"],
                      "bedtime": [23.0, 24.0], "score": [70, 80]})
    b = pd.DataFrame({"day": ["2024-01-02", "2024-01-03"],
                      "bedtime": [24.5, 22.0], "score": [85, 90]})
    merged = storage.upsert(a, b)
    assert len(merged) == 3                      # no duplicate 2024-01-02
    row = merged[merged["day"].astype(str) == "2024-01-02"].iloc[0]
    assert row["score"] == 85                    # newer value wins
