"""Stage 3 tests — site payload shaping and build output."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import compute, site  # noqa: E402
from sleep.schema import LOW  # noqa: E402


def _computed(n=400, seed=5):
    """A small but complete computed frame via the real pipeline."""
    rng = np.random.default_rng(seed)
    days = pd.date_range("2024-01-01", periods=n).date
    history = pd.DataFrame({
        "day": days,
        "bedtime": rng.normal(23.5, 0.8, n).clip(21, 27),
        "waketime": rng.normal(31.0, 0.7, n).clip(29, 34),
        "total_sleep_h": rng.normal(6.8, 0.9, n).clip(4.2, 10),
        "time_in_bed_h": rng.normal(7.6, 0.9, n).clip(5, 11),
        "efficiency": rng.normal(88, 4, n).clip(60, 99),
        "latency_min": rng.normal(12, 6, n).clip(1, 90),
        "restless_periods": rng.integers(5, 30, n),
        "deep_h": rng.normal(1.2, 0.3, n).clip(0.2, 3),
        "rem_h": rng.normal(1.4, 0.35, n).clip(0.2, 3),
        "light_h": rng.normal(4.0, 0.6, n).clip(1, 7),
        "hr_low": rng.normal(50, 3, n).clip(40, 70),
        "hr_avg": rng.normal(58, 3, n).clip(45, 80),
        "hrv": rng.normal(50, 12, n).clip(15, 110),
        "breaths_per_min": rng.normal(13.2, 0.6, n).clip(10, 18),
        "nap_sleep_h": 0.0,
        "steps": rng.integers(4000, 16000, n).astype(float),
        "temp_deviation": rng.normal(0, 0.3, n).clip(-1.5, 1.5),
        "temp_trend_deviation": 0.0,
        "oura_sleep_score": rng.integers(55, 95, n),
        "oura_readiness_score": rng.integers(50, 95, n),
        "restfulness": rng.integers(40, 95, n).astype(float),
    })
    exclusions = pd.DataFrame(columns=["day", "reason", "added_at"])
    daily, summary = compute.compute(history, exclusions)
    return daily, summary


DAILY, SUMMARY = _computed()


def _spec(key):
    return site.PAGE_BY_KEY[key]


# --- percentile direction ----------------------------------------------------

def test_display_pct_inverted_for_lower_is_better():
    # hr_low: a LOW raw value is GOOD, so a raw percentile of 10 must display 90.
    assert site._display_pct(10.0, higher_is_better=False) == 90.0
    assert site._display_pct(10.0, higher_is_better=True) == 10.0


def test_stats_payload_uses_display_direction():
    stats = site._stats_payload(DAILY, _spec("hr_low"))
    raw = DAILY[DAILY["hr_low"].notna()].iloc[-1]["hr_low_pct"]
    assert stats["pct"] == pytest.approx(100 - raw, abs=0.11)


# --- payload structure --------------------------------------------------------

def test_band_columns_only_for_low_confidence():
    low_rows = site._series_rows(DAILY, _spec("deep_h"))     # LOW tier
    high_rows = site._series_rows(DAILY, _spec("hrv"))       # HIGH tier
    assert _spec("deep_h").confidence == LOW
    assert len(low_rows[0]) == 6      # date, v, a7, a30, lo, hi
    assert len(high_rows[0]) == 4     # date, v, a7, a30


def test_series_trimmed_to_first_valid():
    d = DAILY.copy()
    d.loc[d.index[:100], "steps"] = np.nan       # simulate a late-starting metric
    rows = site._series_rows(d, _spec("steps"))
    assert rows[0][0] == d.index[100].strftime("%Y-%m-%d")


def test_metric_payload_complete():
    payload = site.metric_payload(DAILY, _spec("sleep_score"))
    assert payload["meta"]["slug"] == "sleep-score"
    assert payload["series"] and payload["stats"]["value"] is not None
    assert set(payload["agg"]) == {"weekly", "quarterly", "annual"}
    assert set(payload["top_bottom"]) == {"all", "year", "quarter"}
    # weekly rows ~ n/7
    assert len(payload["agg"]["weekly"]) == pytest.approx(len(DAILY) / 7, abs=2)


def test_top_bottom_direction_for_debt():
    payload = site.metric_payload(DAILY, _spec("sleep_debt_h"))
    tb = payload["top_bottom"]["all"]
    best = [v for _, v in tb["top"]]
    worst = [v for _, v in tb["bottom"]]
    assert max(best) <= min(worst), "for debt, 'best' must be the lowest values"


def test_component_meta_present():
    payload = site.metric_payload(DAILY, _spec("hrv"))
    assert payload["meta"]["score_weight"] == 1.0
    assert payload["meta"]["latest_contribution"] is not None


# --- overview -----------------------------------------------------------------

def test_overview_payload_has_all_cards():
    ov = site.overview_payload(DAILY, SUMMARY)
    assert len(ov["cards"]) == len(site.CARD_KEYS)
    for card in ov["cards"]:
        assert card["label"] and card["format"]
        # The trio the overview shows: last night / 7d / 30d, each with a pct.
        for k in ("value", "avg7", "avg30", "pct", "pct7", "pct30"):
            assert k in card, k
        assert "spark" not in card
    assert isinstance(ov["flag"]["raised"], bool)


# --- distribution strip + explanations ---------------------------------------

def test_dist_payload_is_ordered_and_complete():
    d = site._dist_payload(DAILY, "hrv")
    assert d["min"] <= d["p25"] <= d["p50"] <= d["p75"] <= d["max"]
    assert d["n"] == int(DAILY["hrv"].notna().sum())


def test_sleep_score_explains_every_component():
    payload = site.metric_payload(DAILY, _spec("sleep_score"))
    ex = payload["explain"]
    assert ex["kind"] == "score"
    labels = {c["label"] for c in ex["components"]}
    from sleep.schema import SCORE_COMPONENTS
    assert labels == {m.label for m in SCORE_COMPONENTS}
    weights = sum(c["weight"] for c in ex["components"])
    assert weights == pytest.approx(9.0)
    assert all(c["score"] is None or 0 <= c["score"] <= 100 for c in ex["components"])


@pytest.mark.parametrize("key", ["sleep_performance_pct", "sleep_debt_h",
                                 "sleep_need_h", "sri", "readiness"])
def test_every_derived_metric_has_an_explanation(key):
    payload = site.metric_payload(DAILY, _spec(key))
    ex = payload.get("explain")
    assert ex and ex["text"] and ex["formula"] and ex["components"]


def test_raw_metrics_have_no_explanation_block():
    payload = site.metric_payload(DAILY, _spec("hrv"))
    assert "explain" not in payload


def test_badge_labels_are_words_not_ratings():
    # "HIGH" next to a 12:38am bedtime read as a rating of the value.
    assert site.BADGE_LABEL["high"] == "high confidence"
    assert site.BADGE_LABEL["derived"] == "derived metric"
    html = site._metric_page(_spec("bedtime"))
    assert "high confidence" in html and ">HIGH<" not in html


# --- full build ---------------------------------------------------------------

def test_build_site_writes_expected_files(tmp_path, monkeypatch):
    monkeypatch.setattr(site.config, "DOCS_DIR", tmp_path)
    site.build_site(DAILY, SUMMARY)

    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "robots.txt").exists()
    assert (tmp_path / "metrics" / "index.html").exists()
    assert (tmp_path / "data" / "overview.json").exists()

    for spec in site.PAGES:
        assert (tmp_path / "metrics" / f"{spec.slug}.html").exists(), spec.slug
        payload_path = tmp_path / "data" / "m" / f"{spec.slug}.json"
        assert payload_path.exists(), spec.slug
        json.loads(payload_path.read_text())     # valid JSON

    index = (tmp_path / "index.html").read_text()
    assert 'name="robots" content="noindex"' in index
    assert "assets/plotly.min.js" in index


# --- data freshness -----------------------------------------------------------

def _with_partial_tail():
    """A history whose newest row has Oura's daily score but no sleep session.

    This is the real 2026-09-08 case: Oura scores a night before the detailed
    session syncs, so the newest ROW is a day later than the newest NIGHT.
    """
    daily, _ = _computed(n=200)
    history = daily.reset_index()[[
        "day", "bedtime", "waketime", "total_sleep_h", "time_in_bed_h",
        "efficiency", "hr_low", "hrv", "breaths_per_min", "nap_sleep_h",
        "steps", "temp_deviation", "oura_sleep_score", "restfulness",
    ]].copy()
    tail = {c: np.nan for c in history.columns}
    tail.update({"day": history["day"].max() + pd.Timedelta(days=1),
                 "oura_sleep_score": 86.0, "restfulness": 98.0,
                 "temp_deviation": 0.13, "nap_sleep_h": 0.0})
    history = pd.concat([history, pd.DataFrame([tail])], ignore_index=True)
    return compute.compute(history, pd.DataFrame(columns=["day", "reason", "added_at"]))


def test_summary_reports_the_last_night_not_the_last_row():
    daily, summary = _with_partial_tail()
    last_night = daily[daily["total_sleep_h"].notna()].index.max().date()
    assert summary["data_through"] == str(last_night)
    # The header used to end at the newest row, contradicting "latest night".
    assert summary["date_range"].endswith(str(last_night))
    assert str(daily.index.max().date()) != summary["data_through"]


def test_partial_night_is_detected_and_described():
    _, summary = _with_partial_tail()
    partial = summary["latest_partial"]
    assert partial is not None
    assert partial["oura_score"] == 86.0
    assert "total_sleep_h" in partial["missing"] and "hrv" in partial["missing"]


def test_no_partial_night_when_the_newest_row_is_complete():
    assert SUMMARY["latest_partial"] is None
    assert SUMMARY["data_through"] == str(DAILY.index.max().date())


def test_stats_flag_values_carried_onto_an_unrecorded_night():
    daily, _ = _with_partial_tail()
    # Debt carries forward across a night with no recording; the score does not.
    carried = site._stats_payload(daily, _spec("sleep_debt_h"))
    measured = site._stats_payload(daily, _spec("sleep_score"))
    assert carried["carried"] is True
    assert measured["carried"] is False
    assert carried["day"] > measured["day"], "that day gap is what needs labelling"


def test_every_payload_carries_freshness(tmp_path, monkeypatch):
    monkeypatch.setattr(site.config, "DOCS_DIR", tmp_path)
    site.build_site(DAILY, SUMMARY)

    overview = json.loads((tmp_path / "data" / "overview.json").read_text())
    assert overview["fresh"]["data_through"] == SUMMARY["data_through"]
    assert overview["fresh"]["built"].endswith("+00:00"), "must be UTC"
    assert overview["fresh"]["schedule_hours_utc"] == site.SCHEDULE_UTC_HOURS

    # Every metric page needs it too, so the stale banner works without a
    # second fetch on the 26 detail pages.
    for spec in site.PAGES:
        payload = json.loads(
            (tmp_path / "data" / "m" / f"{spec.slug}.json").read_text())
        assert payload["fresh"]["data_through"] == SUMMARY["data_through"], spec.slug


def test_shell_carries_the_stale_bar_and_freshness_line(tmp_path, monkeypatch):
    monkeypatch.setattr(site.config, "DOCS_DIR", tmp_path)
    site.build_site(DAILY, SUMMARY)
    for page in ("index.html", "metrics/sleep-debt-h.html"):
        html = (tmp_path / page).read_text()
        assert 'id="stalebar"' in html, page
        assert 'id="freshline"' in html, page


def test_schedule_hours_match_the_workflow_cron():
    """The footer tells you when to expect an update; a stale promise is worse
    than none, so tie it to the actual cron entries in both directions."""
    from pathlib import Path
    import re
    wf = (Path(__file__).resolve().parents[1]
          / ".github" / "workflows" / "daily.yml").read_text()
    hours = [int(h) for h in re.findall(r'cron:\s*"0 (\d{1,2}) \* \* \*"', wf)]
    assert hours, "no daily cron found in the workflow"
    assert sorted(hours) == sorted(site.SCHEDULE_UTC_HOURS), (
        f"workflow runs at {hours} UTC but the site advertises "
        f"{site.SCHEDULE_UTC_HOURS}")


def test_schedule_hours_travel_in_the_payload():
    """The browser localises these, so they must ship as numbers, not prose."""
    fresh = site._freshness(SUMMARY)
    assert fresh["schedule_hours_utc"] == site.SCHEDULE_UTC_HOURS
    # A plain-text fallback stays for the no-JS case.
    assert "UTC" in fresh["schedule"]
    for h in site.SCHEDULE_UTC_HOURS:
        assert f"{h:02d}:00" in fresh["schedule"]


def test_runs_land_morning_and_evening_eastern():
    """9am and 9pm Eastern, drifting to 8am and 8pm in winter.

    Cron is UTC-only so the hour shifts across DST, which is accepted. The guard
    is that one run stays in the morning and the other in the evening, since a
    single morning pull can beat the ring's sync — which is what lost a night on
    2026-09-08.
    """
    import datetime as dt
    from zoneinfo import ZoneInfo
    eastern = ZoneInfo("America/New_York")
    for month, label in ((7, "EDT"), (1, "EST")):
        local = sorted(
            dt.datetime(2026, month, 15, h, tzinfo=dt.timezone.utc)
            .astimezone(eastern).hour
            for h in site.SCHEDULE_UTC_HOURS
        )
        assert len(local) == 2, "expected a morning and an evening run"
        assert local[0] in (8, 9), f"{label}: morning run at {local[0]}:00 Eastern"
        assert local[1] in (20, 21), f"{label}: evening run at {local[1]}:00 Eastern"
