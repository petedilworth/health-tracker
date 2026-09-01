#!/usr/bin/env python3
"""Statistical validation of the scoring engine.

Answers one question above all: does the custom score actually use its range?
That's the specific failure of Oura's own score that prompted this rebuild.

    python scripts/validate_score.py
"""

import logging
import sys
import warnings
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import compute  # noqa: E402
from sleep.schema import SCORE_COMPONENTS  # noqa: E402

warnings.filterwarnings("ignore")


def line(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def main() -> None:
    logging.basicConfig(level=logging.ERROR)
    daily, summary = compute.compute()
    if daily.empty:
        print("No data — run the Backfill workflow first.")
        return

    print(f"Nights recorded {summary['nights_recorded']}, "
          f"after exclusions {summary['nights_after_exclusions']}, "
          f"scored {summary['nights_scored']}")
    print(f"Range: {summary['date_range']}")

    s = daily["sleep_score"].dropna()
    oura = daily["oura_sleep_score"].dropna()

    # 1. Does the score use its full range?
    line("1. Score distribution (the headline check)")
    rows = []
    for name, series in [("custom sleep_score", s), ("Oura's own score", oura)]:
        if series.empty:
            continue
        rows.append({
            "score": name, "n": len(series),
            "min": series.min(), "p1": series.quantile(.01),
            "p25": series.quantile(.25), "median": series.median(),
            "p75": series.quantile(.75), "p99": series.quantile(.99),
            "max": series.max(), "SD": series.std(),
            "IQR": series.quantile(.75) - series.quantile(.25),
        })
    print(pd.DataFrame(rows).round(1).to_string(index=False))

    # 5. Custom vs Oura
    line("2. Custom vs Oura spread")
    if not oura.empty:
        both = daily[["sleep_score", "oura_sleep_score"]].dropna()
        sd_ratio = s.std() / oura.std() if oura.std() else float("nan")
        iqr_c = s.quantile(.75) - s.quantile(.25)
        iqr_o = oura.quantile(.75) - oura.quantile(.25)
        print(f"  SD:  custom {s.std():.1f}  vs Oura {oura.std():.1f}   "
              f"({sd_ratio:.2f}x wider)")
        print(f"  IQR: custom {iqr_c:.1f}  vs Oura {iqr_o:.1f}   "
              f"({iqr_c / iqr_o:.2f}x wider)" if iqr_o else "")
        print(f"  correlation between them: {both.corr().iloc[0, 1]:.2f} "
              f"(n={len(both)})")

    # 2. Does any single component dominate?
    line("3. Component influence on the final score")
    infl = []
    for m in SCORE_COMPONENTS:
        col = f"c_{m.key}"
        if col not in daily.columns:
            continue
        pair = daily[[col, "sleep_score"]].dropna()
        infl.append({
            "component": m.key, "weight": m.weight, "confidence": m.confidence,
            "n": len(pair),
            "corr_to_score": pair.corr().iloc[0, 1] if len(pair) > 2 else float("nan"),
        })
    infl_df = pd.DataFrame(infl).sort_values("corr_to_score", ascending=False)
    print(infl_df.round(2).to_string(index=False))
    hot = infl_df[infl_df["corr_to_score"] > 0.8]
    print(f"\n  Components correlating >0.8 with the score: "
          f"{'none' if hot.empty else ', '.join(hot['component'])}")

    # 3. Correlation with next-day readiness
    line("4. Score vs next-day readiness")
    pair = daily[["sleep_score", "readiness"]].dropna()
    if len(pair) > 2:
        print(f"  correlation {pair.corr().iloc[0, 1]:.2f} (n={len(pair)})")
        # The score is itself 25% of readiness, so the above is partly circular.
        # Compare against the purely physiological part for an honest read.
        phys = [c for c in ("hrv_z", "hr_low_z", "breaths_per_min_z")
                if c in daily.columns]
        if phys:
            indep = daily[["sleep_score"] + phys].dropna()
            corrs = {c: indep["sleep_score"].corr(indep[c]) for c in phys}
            shown = ", ".join(f"{c.replace('_z','')} {v:+.2f}" for c, v in corrs.items())
            print(f"  vs physiology alone (non-circular): {shown}")

    # 4. Percentile behaviour
    line("5. Percentile columns")
    for col in ["sleep_score_pct", "sleep_debt_h_pct", "sri_pct"]:
        if col in daily.columns:
            p = daily[col].dropna()
            if not p.empty:
                print(f"  {col:22s} min {p.min():5.1f}  max {p.max():5.1f}  n={len(p)}")

    # 6. Derived-series sanity
    line("6. Derived series sanity")
    checks = []
    debt, need = daily["sleep_debt_h"].dropna(), daily["sleep_need_h"].dropna()
    sri, perf = daily["sri"].dropna(), daily["sleep_performance_pct"].dropna()
    checks.append(("sleep_debt_h >= 0", bool((debt >= 0).all()),
                   f"min {debt.min():.2f}, median {debt.median():.2f}, max {debt.max():.2f}"))
    checks.append(("sleep_need_h within 6-11h", bool(need.between(6, 11).all()),
                   f"min {need.min():.2f}, median {need.median():.2f}, max {need.max():.2f}"))
    checks.append(("sri within 0-100", bool(sri.between(0, 100).all()),
                   f"min {sri.min():.1f}, median {sri.median():.1f}, max {sri.max():.1f}"))
    checks.append(("performance % within 0-100", bool(perf.between(0, 100).all()),
                   f"median {perf.median():.1f}"))
    flag_rate = daily["flag_raised"].mean() * 100
    checks.append(("health flag fires 1-15% of nights", 1 <= flag_rate <= 15,
                   f"{flag_rate:.1f}% ({int(daily['flag_raised'].sum())} nights)"))
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:34s} {detail}")

    line("Latest night")
    for k, v in (summary.get("latest") or {}).items():
        print(f"  {k:24s} {v}")


if __name__ == "__main__":
    main()
