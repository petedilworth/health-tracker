#!/usr/bin/env python3
"""Compare sleep-need baseline options against your actual history.

The need baseline is a judgement call, not a measurement: it asks how much of
your sleep pattern is restriction versus requirement. It is the given
percentile of ALL recorded sleep (not a rolling window, which would let a bad
stretch lower the bar). This prints the same table for several candidate
percentiles so the choice can be re-argued with current data.

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

CANDIDATES = [0.90, 0.80, 0.75, 0.70]


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

    current = score.NEED_QUANTILE
    rows = []
    try:
        for q in CANDIDATES:
            score.NEED_QUANTILE = q
            out = score.sleep_debt_and_need(daily)
            need, debt = out["sleep_need_h"], out["sleep_debt_h"]
            perf = out["sleep_performance_pct"]
            rows.append({
                "quantile": f"P{int(q * 100)}",
                "in_use": q == current,
                "need_median_h": round(float(need.median()), 2),
                "shortfall_median_h": round(
                    float((need - daily["total_sleep_h"]).median()), 2),
                "debt_median_h": round(float(debt.median()), 2),
                "debt_max_h": round(float(debt.max()), 2),
                "performance_median_pct": round(float(perf.median()), 1),
                "nights_meeting_need_pct": round(
                    float((daily["total_sleep_h"] >= need).mean() * 100), 1),
            })
    finally:
        score.NEED_QUANTILE = current

    nights = int(daily["total_sleep_h"].notna().sum())
    span = f"{daily.index.min().date()} → {daily.index.max().date()}"

    if args.markdown:
        print(f"Based on **{nights} nights** ({span}). "
              f"Currently in use: **P{int(current * 100)}**.\n")
        print("| Baseline | Need (median) | Nightly shortfall | Debt median / max "
              "| Performance | Nights meeting need |")
        print("|---|---|---|---|---|---|")
        for r in rows:
            mark = " ← in use" if r["in_use"] else ""
            print(f"| {r['quantile']}{mark} | {r['need_median_h']}h | "
                  f"+{r['shortfall_median_h']}h | {r['debt_median_h']}h / "
                  f"{r['debt_max_h']}h | {r['performance_median_pct']}% | "
                  f"{r['nights_meeting_need_pct']}% |")
        print("\nA higher percentile assumes more of your current pattern is "
              "restriction; a lower one assumes more of it is your actual "
              "requirement. If the nights-meeting-need figure has drifted far "
              "from ~20-25%, the baseline is worth moving.")
    else:
        print(f"{nights} nights ({span}), currently using P{int(current * 100)}\n")
        for r in rows:
            mark = "  <- in use" if r["in_use"] else ""
            print(f"{r['quantile']}: need med {r['need_median_h']:.2f}h | "
                  f"shortfall {r['shortfall_median_h']:+.2f}h | "
                  f"debt med {r['debt_median_h']:5.2f}h (max {r['debt_max_h']:5.2f}) | "
                  f"perf med {r['performance_median_pct']:.1f}% | "
                  f"meeting need {r['nights_meeting_need_pct']:.1f}%{mark}")


if __name__ == "__main__":
    main()
