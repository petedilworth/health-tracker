"""Stage 1 tests — transform, store and exclusions. No network required."""

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import store, transform  # noqa: E402
from sleep.schema import (  # noqa: E402
    ALL_METRICS, RAW_COLUMNS, SCORE_COMPONENTS, TOTAL_SCORE_WEIGHT,
)


def _night(day, start, end, **kw):
    base = {
        "day": day, "type": "long_sleep",
        "bedtime_start": start, "bedtime_end": end,
        "total_sleep_duration": 7 * 3600, "time_in_bed": 8 * 3600,
        "deep_sleep_duration": 3600, "rem_sleep_duration": 5400,
        "light_sleep_duration": 4 * 3600, "latency": 900,
        "efficiency": 92, "restless_periods": 14,
        "lowest_heart_rate": 48, "average_heart_rate": 55,
        "average_hrv": 61, "average_breath": 14.2,
    }
    base.update(kw)
    return base


# --- bedtime handling -------------------------------------------------------

def test_bedtime_after_midnight_shift():
    df = transform.sleep_sessions_to_frame([
        _night("2024-01-01", "2024-01-01T23:30:00-05:00", "2024-01-02T07:00:00-05:00"),
        _night("2024-01-02", "2024-01-03T01:00:00-05:00", "2024-01-03T08:00:00-05:00"),
    ])
    bt = dict(zip(df["day"].astype(str), df["bedtime"]))
    assert bt["2024-01-01"] == 23.5   # evening stays as-is
    assert bt["2024-01-02"] == 25.0   # 1am becomes 25.0, not 1.0


def test_bedtime_ignores_utc_offset_travel():
    """Travel changes the UTC offset; local wall-clock bedtime is what matters."""
    df = transform.sleep_sessions_to_frame([
        _night("2024-01-01", "2024-01-01T23:00:00-05:00", "2024-01-02T07:00:00-05:00"),
        _night("2024-01-02", "2024-01-02T23:00:00+09:00", "2024-01-03T07:00:00+09:00"),
    ])
    # Both went to bed at 23:00 local, so both must read 23.0.
    assert set(df["bedtime"]) == {23.0}


# --- naps -------------------------------------------------------------------

def test_naps_excluded_from_night_but_summed_separately():
    df = transform.sleep_sessions_to_frame([
        _night("2024-01-01", "2024-01-01T23:00:00-05:00", "2024-01-02T07:00:00-05:00"),
        {"day": "2024-01-01", "type": "late_nap",
         "bedtime_start": "2024-01-01T14:00:00-05:00",
         "bedtime_end": "2024-01-01T15:00:00-05:00",
         "total_sleep_duration": 3600},
    ])
    assert len(df) == 1
    row = df.iloc[0]
    assert row["bedtime"] == 23.0          # the nap did not become the night
    assert row["nap_sleep_h"] == pytest.approx(1.0)  # but it is counted for debt


def test_duration_conversion_to_hours():
    df = transform.sleep_sessions_to_frame([
        _night("2024-01-01", "2024-01-01T23:00:00-05:00", "2024-01-02T07:00:00-05:00")
    ])
    row = df.iloc[0]
    assert row["total_sleep_h"] == pytest.approx(7.0)
    assert row["time_in_bed_h"] == pytest.approx(8.0)
    assert row["deep_h"] == pytest.approx(1.0)
    assert row["rem_h"] == pytest.approx(1.5)
    assert row["latency_min"] == pytest.approx(15.0)


# --- multi-endpoint merge ---------------------------------------------------

def test_build_rows_merges_all_endpoints():
    rows = transform.build_rows({
        "sleep": [_night("2024-01-01", "2024-01-01T23:00:00-05:00",
                         "2024-01-02T07:00:00-05:00")],
        "daily_sleep": [{"day": "2024-01-01", "score": 78,
                         "contributors": {"restfulness": 65}}],
        "daily_activity": [{"day": "2024-01-01", "steps": 11034}],
        "daily_readiness": [{"day": "2024-01-01", "score": 80,
                             "temperature_deviation": 0.42,
                             "temperature_trend_deviation": 0.15}],
    })
    assert list(rows.columns) == RAW_COLUMNS
    row = rows.iloc[0]
    assert row["steps"] == 11034
    assert row["oura_sleep_score"] == 78
    assert row["restfulness"] == 65
    assert row["temp_deviation"] == pytest.approx(0.42)
    assert row["hrv"] == 61


def test_build_rows_handles_empty_payloads():
    rows = transform.build_rows({"sleep": [], "daily_sleep": [],
                                 "daily_activity": [], "daily_readiness": []})
    assert rows.empty
    assert list(rows.columns) == RAW_COLUMNS


# --- storage ----------------------------------------------------------------

def test_upsert_is_idempotent_and_newest_wins():
    a = pd.DataFrame({"day": [dt.date(2024, 1, 1), dt.date(2024, 1, 2)],
                      "total_sleep_h": [7.0, 6.0]})
    b = pd.DataFrame({"day": [dt.date(2024, 1, 2), dt.date(2024, 1, 3)],
                      "total_sleep_h": [6.5, 8.0]})
    merged = store.upsert(a, b)
    assert len(merged) == 3                                    # no duplicate day
    assert merged.set_index("day").loc[dt.date(2024, 1, 2), "total_sleep_h"] == 6.5

    # Re-applying the same batch must change nothing.
    assert store.upsert(merged, b).equals(merged)


def test_exclusions_roundtrip(tmp_path):
    path = tmp_path / "exclusions.csv"
    store.add_exclusion(path, dt.date(2024, 5, 1), "ring not charged")
    store.add_exclusion(path, dt.date(2024, 5, 2), "not worn")
    rows = store.load_exclusions(path)
    assert len(rows) == 2

    # Re-excluding the same day updates rather than duplicating.
    store.add_exclusion(path, dt.date(2024, 5, 1), "corrected reason")
    rows = store.load_exclusions(path)
    assert len(rows) == 2
    assert rows.set_index("day").loc[dt.date(2024, 5, 1), "reason"] == "corrected reason"

    store.remove_exclusion(path, dt.date(2024, 5, 1))
    assert len(store.load_exclusions(path)) == 1


def test_apply_exclusions_drops_days():
    history = pd.DataFrame({
        "day": [dt.date(2024, 5, 1), dt.date(2024, 5, 2), dt.date(2024, 5, 3)],
        "total_sleep_h": [7.0, 0.4, 8.0],
    })
    exclusions = pd.DataFrame({"day": [dt.date(2024, 5, 2)],
                               "reason": ["ring died"], "added_at": ["now"]})
    kept = store.apply_exclusions(history, exclusions)
    assert len(kept) == 2
    assert dt.date(2024, 5, 2) not in set(kept["day"])


# --- schema integrity -------------------------------------------------------

def test_score_weights_match_plan():
    weights = {m.key: m.weight for m in SCORE_COMPONENTS}
    assert weights["rem_h"] == 0.25 and weights["deep_h"] == 0.25   # low confidence
    assert weights["efficiency"] == 0.5 and weights["restfulness"] == 0.5
    assert weights["hrv"] == 1.0 and weights["hr_low"] == 1.0
    assert weights["time_in_bed_h"] == 1.0
    assert TOTAL_SCORE_WEIGHT == pytest.approx(8.5)


def test_every_metric_key_is_a_stored_column():
    # Guards against a metric being defined but never populated by the ingest.
    derived = {"timing"}  # computed in Stage 2, not pulled from the API
    for metric in ALL_METRICS:
        if metric.key in derived:
            continue
        assert metric.key in RAW_COLUMNS, f"{metric.key} missing from RAW_COLUMNS"
