#!/usr/bin/env python3
"""Compare sleep-need baseline options against your actual history.

The need baseline is a judgement call, not a measurement: it asks how much of
your sleep pattern is restriction versus requirement. It is the median sleep you
achieve on nights with at least NEED_OPPORTUNITY_TIB_H in bed, so a tightening
schedule removes nights from the estimate rather than dragging it lower. This
prints the same table for several candidate opportunity thresholds, plus the
plain median and P75 of all sleep as reference rows, so the choice can be
re-argued with current data.

Raising the threshold conditions on less restricted nights, but leaves fewer of
them and leans harder on the ring's weakest measurement, wake detection on long
nights in bed.

Run any time, or automatically each March by the recalibration-review workflow.

    python scripts/need_calibration.py
    python scripts/need_calibration.py --markdown
"""

import argparse
import logging
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import config, metrics, quality, score, store  # noqa: E402

warnings.filterwarnings("ignore")

CANDIDATES = [7.5, 8.0, 8.5, 9.0]       # hours in bed
QUANTILE_REFERENCES = [0.50, 0.75]      # the old behaviour, for comparison


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare need-baseline options.")
    parser.add_argument("--markdown", action="store_true",
                        help="Emit a markdown table (used by the review workflow).")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)

    history = store.load_history(config.HISTORY_PATH)
    if history.empty:
        print("No history found — run the Backfill workflow first.")
        return

    history = store.apply_exclusions(
        history, store.load_exclusions(config.EXCLUSIONS_PATH)
    )
    history, _ = quality.clamp_implausible(history)
    daily = metrics.to_daily(history)

    def summarise(label: str, in_use: bool, qualifying: int | None) -> dict:
        out = score.sleep_debt_and_need(daily)
        need, debt = out["sleep_need_h"], out["sleep_debt_h"]
        perf = out["sleep_performance_pct"]
        return {
            "label": label,
            "in_use": in_use,
            "qualifying": qualifying,
            "need_median_h": round(float(need.median()), 2),
            "shortfall_median_h": round(
                float((need - daily["total_sleep_h"]).median()), 2),
            "debt_median_h": round(float(debt.median()), 2),
            "debt_max_h": round(float(debt.max()), 2),
            "performance_median_pct": round(float(perf.median()), 1),
            "nights_meeting_need_pct": round(
                float((daily["total_sleep_h"] >= need).mean() * 100), 1),
        }

    current_tib = score.NEED_OPPORTUNITY_TIB_H
    current_min = score.NEED_MIN_OPPORTUNITY_NIGHTS
    current_q = score.NEED_QUANTILE

    rows = []
    try:
        for tib in CANDIDATES:
            score.NEED_OPPORTUNITY_TIB_H = tib
            qualifying = int(daily["total_sleep_h"].where(
                daily["time_in_bed_h"] >= tib).notna().sum())
            rows.append(summarise(f"time in bed >= {tib:g}h",
                                  tib == current_tib, qualifying))
        # Force the fallback path, to show what a plain quantile would give.
        score.NEED_MIN_OPPORTUNITY_NIGHTS = 10 ** 9
        for q in QUANTILE_REFERENCES:
            score.NEED_QUANTILE = q
            rows.append(summarise(f"P{int(q * 100)} of all sleep", False, None))
    finally:
        score.NEED_OPPORTUNITY_TIB_H = current_tib
        score.NEED_MIN_OPPORTUNITY_NIGHTS = current_min
        score.NEED_QUANTILE = current_q

    nights = int(daily["total_sleep_h"].notna().sum())
    span = f"{daily.index.min().date()} → {daily.index.max().date()}"
    in_use = f"time in bed >= {current_tib:g}h"

    if args.markdown:
        print(f"Based on **{nights} nights** ({span}). "
              f"Currently in use: **{in_use}**.\n")
        print("| Baseline | Qualifying nights | Need (median) | Nightly shortfall "
              "| Debt median / max | Performance | Nights meeting need |")
        print("|---|---|---|---|---|---|---|")
        for r in rows:
            mark = " ← in use" if r["in_use"] else ""
            n = r["qualifying"] if r["qualifying"] is not None else "—"
            print(f"| {r['label']}{mark} | {n} | {r['need_median_h']}h | "
                  f"+{r['shortfall_median_h']}h | {r['debt_median_h']}h / "
                  f"{r['debt_max_h']}h | {r['performance_median_pct']}% | "
                  f"{r['nights_meeting_need_pct']}% |")
        print("\nThe two quantile rows are the old behaviour, kept for "
              "comparison: they track your schedule, so they fall when your "
              "sleep does. If the nights-meeting-need figure has drifted far "
              "from ~20-25%, or the threshold rows have spread apart, the "
              "baseline is worth moving.")
    else:
        print(f"{nights} nights ({span}), currently using {in_use}\n")
        for r in rows:
            mark = "  <- in use" if r["in_use"] else ""
            n = f"{r['qualifying']:4d}" if r["qualifying"] is not None else "   —"
            print(f"{r['label']:22s} n={n} | need med {r['need_median_h']:.2f}h | "
                  f"shortfall {r['shortfall_median_h']:+.2f}h | "
                  f"debt med {r['debt_median_h']:5.2f}h (max {r['debt_max_h']:5.2f}) | "
                  f"perf med {r['performance_median_pct']:.1f}% | "
                  f"meeting need {r['nights_meeting_need_pct']:.1f}%{mark}")


if __name__ == "__main__":
    main()
