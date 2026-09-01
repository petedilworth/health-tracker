"""Regression tests for the pre-Stage-3 code review findings."""

import datetime as dt
import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import quality, store, transform  # noqa: E402
from sleep.client import OuraClient  # noqa: E402


# Finding 1 — upsert must merge fields, not replace rows wholesale.

def test_upsert_preserves_fields_the_new_pull_is_missing():
    existing = pd.DataFrame({
        "day": [dt.date(2026, 8, 1)],
        "total_sleep_h": [7.0],
        "temp_deviation": [0.35],       # readiness data we already stored
    })
    fresh = pd.DataFrame({
        "day": [dt.date(2026, 8, 1)],
        "total_sleep_h": [7.1],         # a corrected value — should win
        "temp_deviation": [np.nan],     # endpoint hiccup — must NOT erase
    })
    merged = store.upsert(existing, fresh)
    row = merged.set_index("day").loc[dt.date(2026, 8, 1)]
    assert row["total_sleep_h"] == 7.1
    assert row["temp_deviation"] == 0.35


# Finding 2 — a recent slump must not trigger truncation of good history.

def test_reliable_start_refuses_to_cut_after_a_late_slump():
    """Years of good steps, then an injury month: keep everything."""
    days = pd.date_range("2022-01-01", periods=700).date
    steps = np.random.default_rng(4).integers(9000, 14000, 700).astype(float)
    steps[600:640] = 500.0              # a bad stretch long after good data
    df = pd.DataFrame({"day": days, "steps": steps})
    assert quality.detect_reliable_start(df, "steps") is None


def test_reliable_start_still_cuts_a_genuinely_bad_early_era():
    days = pd.date_range("2019-01-01", periods=600).date
    steps = np.concatenate([np.full(300, 100.0), np.full(300, 11000.0)])
    df = pd.DataFrame({"day": days, "steps": steps})
    assert quality.detect_reliable_start(df, "steps") is not None


# Finding 3 — a naps-only payload must keep its nap hours.

def test_nap_only_payload_retains_nap_hours():
    rows = transform.build_rows({
        "sleep": [{
            "day": "2026-08-01", "type": "late_nap",
            "bedtime_start": "2026-08-01T14:00:00-05:00",
            "bedtime_end": "2026-08-01T15:30:00-05:00",
            "total_sleep_duration": 5400,
        }],
        "daily_sleep": [], "daily_activity": [], "daily_readiness": [],
    })
    assert len(rows) == 1
    assert rows["nap_sleep_h"].iloc[0] == pytest.approx(1.5)


# Finding 6 — 429 retries; other 4xx still fail fast.

def _response(status: int) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = b'{"data": [], "next_token": null}'
    return resp


def test_client_retries_429_then_succeeds():
    client = OuraClient("token")
    responses = [_response(429), _response(200)]
    with mock.patch.object(client._session, "get", side_effect=responses), \
         mock.patch("sleep.client.time.sleep") as slept:
        out = client._get("https://example.test", {})
    assert out == {"data": [], "next_token": None}
    assert slept.called


def test_client_fails_fast_on_401():
    client = OuraClient("token")
    with mock.patch.object(client._session, "get",
                           return_value=_response(401)) as get, \
         mock.patch("sleep.client.time.sleep"):
        with pytest.raises(requests.HTTPError):
            client._get("https://example.test", {})
    assert get.call_count == 1, "auth failures must not be retried"


# Finding 8 — a ±1h mismatch is only benign in a DST month.

def _nights(dates, overrides=None):
    base = {"bedtime": 23.0, "waketime": 31.0, "total_sleep_h": 7.0,
            "time_in_bed_h": 8.0, "efficiency": 90.0, "hrv": 55.0,
            "hr_low": 50.0, "temp_deviation": 0.0, "steps": 10000.0}
    rows = [{"day": d, **base} for d in dates]
    for i, ov in (overrides or {}).items():
        rows[i].update(ov)
    return pd.DataFrame(rows)


def test_one_hour_mismatch_in_dst_month_is_benign():
    dates = [dt.date(2024, 10, 24 + i) for i in range(6)]
    df = _nights(dates, {3: {"time_in_bed_h": 9.0}})   # -1h mismatch, October
    out = quality.find_anomalies(df)
    hit = out[out["day"] == dates[3]]
    assert bool(hit.iloc[0]["benign"]) is True


def test_one_hour_mismatch_in_july_needs_review():
    dates = [dt.date(2024, 7, 10 + i) for i in range(6)]
    df = _nights(dates, {3: {"time_in_bed_h": 9.0}})   # -1h mismatch, July
    out = quality.find_anomalies(df)
    hit = out[out["day"] == dates[3]]
    assert len(hit) == 1
    assert bool(hit.iloc[0]["benign"]) is False
    assert "mismatch" in hit.iloc[0]["reasons"]
