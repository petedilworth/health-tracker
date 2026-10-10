"""The bedtime game: ladder, personal bests, moving line."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import bedtime  # noqa: E402


def _frame(bedtimes, start="2026-10-11", tib=None):
    idx = pd.date_range(start, periods=len(bedtimes), freq="D", name="day")
    df = pd.DataFrame({"bedtime": bedtimes}, index=idx)
    df["time_in_bed_h"] = tib if tib is not None else 7.5
    df["total_sleep_h"] = 6.5
    return df


def test_target_line_derives_from_the_declared_opportunity():
    # 06:15 wake, 8h in bed -> lights out 22:15, and the ladder ends there.
    assert bedtime.TARGET_H == 22.25
    assert bedtime.LADDER_H == [23.0, 22.75, 22.5, 22.25]


def test_ladder_counts_nights_by_each_rung_in_the_last_30():
    bt = [23.5] * 20 + [22.0] * 5 + [22.6] * 5     # 5 by 22:15, 10 by 23:00
    lad = bedtime.ladder(_frame(bt))
    counts = {r["line"]: r["count"] for r in lad["rungs"]}
    assert counts[22.25] == 5 and counts[22.5] == 5
    assert counts[22.75] == 10 and counts[23.0] == 10
    assert lad["nights"] == 30


def test_ladder_ignores_trailing_rows_with_no_bedtime():
    # The frame is reindexed to every day; a score-only row after the last
    # night must not count as a missed night.
    bt = [22.0] * 30 + [np.nan]
    lad = bedtime.ladder(_frame(bt))
    assert lad["rungs"][-1]["count"] == 30 and lad["nights"] == 30


def test_ladder_best_30_is_the_record_not_the_current_window():
    bt = [22.0] * 30 + [23.9] * 30
    lad = bedtime.ladder(_frame(bt))
    rung = lad["rungs"][-1]
    assert rung["count"] == 0 and rung["best"] == 30
    assert rung["best_ended"] == "2026-11-09"


def test_best_runs_track_longest_and_current_and_missing_nights_break_them():
    bt = [22.0, 22.0, 22.0, np.nan, 22.0, 22.0, 23.9, 22.1]
    out = bedtime.bests(_frame(bt))
    by_target = next(b for b in out if b["line"] == bedtime.TARGET_H)
    assert by_target["best"] == 3 and by_target["best_ended"] == "2026-10-13"
    assert by_target["current"] == 1
    tib = next(b for b in out if b["kind"] == "hours")
    assert tib["line"] == 8.0 and tib["best"] == 0     # frame has 7.5h everywhere


def test_line_moves_after_two_qualifying_weeks_and_stops_at_the_floor():
    # Three weeks of 5/7 by 23:00 -> one move after week 2, week 3 banks one.
    week = [22.9] * 5 + [23.5] * 2
    out = bedtime.moving_line(_frame(week * 3))
    assert out["line"] == 22.75
    assert out["moves"] == [{"day": "2026-10-25", "to": 22.75}]
    assert out["week"] is None
    # Week 3 was judged against the new line, and 22:54 no longer makes it.
    assert [w["ok"] for w in out["weeks"]] == [True, True, False]
    assert out["weeks"][2]["hits"] == 0 and out["weeks"][2]["line"] == 22.75
    assert out["banked"] == 0

    # Hitting every week walks it to the floor and no further.
    out = bedtime.moving_line(_frame([22.0] * 7 * 12))
    assert out["line"] == bedtime.LINE_FLOOR_H and out["next"] is None
    assert len(out["moves"]) == 3


def test_line_reports_the_week_in_progress():
    bt = [22.9, 23.5, 22.9]      # three nights into week 1, two hits
    out = bedtime.moving_line(_frame(bt))
    assert out["line"] == 23.0 and out["weeks"] == []
    assert out["week"] == {"n": 1, "hits": 2, "nights": 3, "misses_left": 1,
                           "ends": "2026-10-17"}


def test_line_not_started_before_the_start_day():
    out = bedtime.moving_line(_frame([22.0] * 10, start="2026-09-01"))
    assert out["line"] == 23.0 and out["week"] is None and out["weeks"] == []


def test_payload_is_json_safe():
    import json
    json.dumps(bedtime.payload(_frame([22.5] * 40)))
