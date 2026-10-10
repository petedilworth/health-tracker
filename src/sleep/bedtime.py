"""The bedtime game.

Three mechanics, chosen 2026-10 to make an earlier bedtime feel like winning
rather than like a target missed 29 nights in 30:

1. A rolling 30-night ladder. Four lines, 23:00 down to the 22:15 target, and
   a count of nights in bed by each. Most nights score on some rung, so the
   counter moves, and 22:15 stays the headline without reading zero.
2. Personal bests. Longest runs ever in bed by 23:00, by the target, and with
   8h in bed, plus the best 30-night count on each rung. A number you once
   set is a cleaner thing to beat than an abstract target.
3. A line that earns its way earlier. It starts at 23:00 on a fixed date and
   moves 15 minutes earlier every time two consecutive 7-night weeks hit it on
   5 nights or more, down to the target. Habit shaping: the line is always
   within reach because it only ever moves after it has been met.

Everything keys off `bedtime` (decimal hour, +24 after midnight, local time)
and `time_in_bed_h`, the two measurements the ring gets right. "By" a line
means bedtime <= line. All nights count, weekends included: the ladder is a
habit measure and a habit has no weekends.

The line is recomputed from history on every build, so it is reproducible
and never depends on a file remembering where it got to.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from . import config

WINDOW_NIGHTS = 30

# The target line, from the declared opportunity: up at 06:15 with 8h in bed
# puts lights-out at 22:15 (24h-shifted decimal: 22.25).
TARGET_H = config.TARGET_WAKE_WEEKDAY_H + 24.0 - config.TARGET_TIB_H
# Rungs 45, 30 and 15 minutes above the target, then the target itself.
LADDER_H = [TARGET_H + 0.75, TARGET_H + 0.5, TARGET_H + 0.25, TARGET_H]

# The moving line.
# Rows are wake dates: this row is the night of 10 -> 11 October 2026, the
# first night after the game was set up.
LINE_START_DAY = dt.date(2026, 10, 11)
LINE_START_H = TARGET_H + 0.75      # 23:00
LINE_STEP_H = 0.25                  # 15 minutes earlier per move
LINE_WEEK_NIGHTS = 7
LINE_WEEK_HITS = 5                  # of 7
LINE_WEEKS_TO_MOVE = 2              # consecutive qualifying weeks per move
LINE_FLOOR_H = TARGET_H

BEST_TIB_H = config.TARGET_TIB_H


def _nights(daily: pd.DataFrame) -> pd.DataFrame:
    """Rows up to the last night with a bedtime; the frame is reindexed to
    every calendar day, so trailing score-only rows would otherwise count as
    misses for a night that has not happened yet."""
    if "bedtime" not in daily.columns or daily.empty:
        return daily.iloc[0:0]
    last = daily["bedtime"].last_valid_index()
    return daily.iloc[0:0] if last is None else daily.loc[:last]


def _hit(series: pd.Series, line: float) -> pd.Series:
    return (series <= line).fillna(False)


def _run_lengths(hit: pd.Series) -> pd.Series:
    """Length of the run ending at each row (0 where not hit)."""
    out, run = [], 0
    for h in hit.tolist():
        run = run + 1 if h else 0
        out.append(run)
    return pd.Series(out, index=hit.index)


def _best_run(hit: pd.Series) -> dict:
    if hit.empty:
        return {"best": 0, "best_ended": None, "current": 0}
    runs = _run_lengths(hit)
    best = int(runs.max())
    ended = runs.idxmax().strftime("%Y-%m-%d") if best else None
    return {"best": best, "best_ended": ended, "current": int(runs.iloc[-1])}


def ladder(daily: pd.DataFrame) -> dict:
    """Nights in the last 30 in bed by each rung, with the best 30-night count
    ever on the same rung so the counter has something to beat."""
    nights = _nights(daily)
    window = nights.tail(WINDOW_NIGHTS)
    rungs = []
    for line in LADDER_H:
        hit_all = _hit(nights["bedtime"], line) if len(nights) else pd.Series(dtype=bool)
        rolling = hit_all.astype(int).rolling(WINDOW_NIGHTS, min_periods=WINDOW_NIGHTS).sum()
        best = int(rolling.max()) if rolling.notna().any() else 0
        rungs.append({
            "line": line,
            "count": int(_hit(window["bedtime"], line).sum()) if len(window) else 0,
            "best": best,
            "best_ended": rolling.idxmax().strftime("%Y-%m-%d") if best else None,
        })
    return {
        "window": WINDOW_NIGHTS,
        "nights": int(window["bedtime"].notna().sum()) if len(window) else 0,
        "rungs": rungs,
    }


def bests(daily: pd.DataFrame) -> list[dict]:
    """Longest runs ever, and where you stand now, on three lines."""
    nights = _nights(daily)
    if nights.empty:
        return []
    out = []
    for label, series, line, kind in [
        ("In bed by", nights["bedtime"], LADDER_H[0], "clock"),
        ("In bed by", nights["bedtime"], TARGET_H, "clock"),
        ("In bed for", nights.get("time_in_bed_h", pd.Series(dtype=float, index=nights.index)),
         BEST_TIB_H, "hours"),
    ]:
        hit = (series >= line).fillna(False) if kind == "hours" else _hit(series, line)
        out.append({"label": label, "line": line, "kind": kind, **_best_run(hit)})
    return out


def moving_line(daily: pd.DataFrame) -> dict:
    """Where the line is, how this week stands, and what moves it next."""
    nights = _nights(daily)
    start = pd.Timestamp(LINE_START_DAY)
    line = LINE_START_H
    banked = 0
    moves: list[dict] = []
    weeks: list[dict] = []
    current = None

    if not nights.empty and nights.index.max() >= start:
        through = nights.index.max()
        k = 0
        while True:
            w_start = start + pd.Timedelta(days=LINE_WEEK_NIGHTS * k)
            w_end = w_start + pd.Timedelta(days=LINE_WEEK_NIGHTS - 1)
            if w_start > through:
                break
            block = nights.loc[w_start:w_end, "bedtime"]
            hits = int(_hit(block, line).sum())
            elapsed = int((through - w_start).days) + 1 if w_end > through else LINE_WEEK_NIGHTS
            if w_end <= through:
                ok = hits >= LINE_WEEK_HITS
                weeks.append({"n": k + 1, "hits": hits, "ok": ok, "line": line,
                              "ends": w_end.strftime("%Y-%m-%d")})
                banked = banked + 1 if ok else 0
                if banked >= LINE_WEEKS_TO_MOVE and line > LINE_FLOOR_H + 1e-9:
                    line = max(LINE_FLOOR_H, round(line - LINE_STEP_H, 4))
                    moves.append({"day": (w_end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                                  "to": line})
                    banked = 0
            else:
                current = {"n": k + 1, "hits": hits, "nights": elapsed,
                           "misses_left": max(0, LINE_WEEK_NIGHTS - LINE_WEEK_HITS
                                              - (elapsed - hits)),
                           "ends": w_end.strftime("%Y-%m-%d")}
            k += 1

    at_floor = line <= LINE_FLOOR_H + 1e-9
    return {
        "line": line,
        "floor": LINE_FLOOR_H,
        "next": None if at_floor else max(LINE_FLOOR_H, round(line - LINE_STEP_H, 4)),
        "started": LINE_START_DAY.isoformat(),
        "step_min": int(round(LINE_STEP_H * 60)),
        "week_nights": LINE_WEEK_NIGHTS,
        "week_hits": LINE_WEEK_HITS,
        "weeks_to_move": LINE_WEEKS_TO_MOVE,
        "banked": banked,
        "week": current,
        "weeks": weeks[-8:],
        "moves": moves,
    }


def payload(daily: pd.DataFrame) -> dict:
    return {"ladder": ladder(daily), "bests": bests(daily), "line": moving_line(daily)}
