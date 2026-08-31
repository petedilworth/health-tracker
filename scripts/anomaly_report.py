#!/usr/bin/env python3
"""Build the one-time anomaly review queue.

Writes:
  docs/review/anomalies.md  — human-readable, grouped, with surrounding nights
                              for context. Readable on github.com from a phone.
  data/anomalies.csv        — the same set, machine-readable.

Run from Actions ("Anomaly report") or locally:
    python scripts/anomaly_report.py
"""

import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import config, quality, store  # noqa: E402

CONTEXT_NIGHTS = 3


def fmt_clock(v) -> str:
    """25.5 -> '1:30am'. Keeps the +24 convention readable."""
    if pd.isna(v):
        return "—"
    h = float(v) % 24
    hour, minute = int(h), int(round((h - int(h)) * 60))
    if minute == 60:
        hour, minute = (hour + 1) % 24, 0
    suffix = "am" if hour < 12 else "pm"
    return f"{hour % 12 or 12}:{minute:02d}{suffix}"


def fmt(v, nd=1) -> str:
    return "—" if pd.isna(v) else f"{float(v):.{nd}f}"


def context_table(history: pd.DataFrame, day) -> str:
    """A small table of the flagged night plus the nights either side."""
    idx = history.index[history["day"] == day]
    if len(idx) == 0:
        return ""
    i = idx[0]
    lo, hi = max(0, i - CONTEXT_NIGHTS), min(len(history), i + CONTEXT_NIGHTS + 1)
    rows = [
        "| Night | Bedtime | Wake | Sleep | In bed | Eff | HRV | HR | Temp | Steps |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for j in range(lo, hi):
        r = history.iloc[j]
        marker = " **←**" if r["day"] == day else ""
        rows.append(
            f"| {r['day']}{marker} | {fmt_clock(r['bedtime'])} | "
            f"{fmt_clock(r['waketime'])} | {fmt(r['total_sleep_h'])}h | "
            f"{fmt(r['time_in_bed_h'])}h | {fmt(r['efficiency'], 0)}% | "
            f"{fmt(r['hrv'], 0)} | {fmt(r['hr_low'], 0)} | "
            f"{fmt(r['temp_deviation'], 2)} | {fmt(r['steps'], 0)} |"
        )
    return "\n".join(rows)


def build_markdown(anomalies: pd.DataFrame, history: pd.DataFrame,
                   starts: dict, dropped: dict) -> str:
    review = anomalies[~anomalies["benign"]]
    benign = anomalies[anomalies["benign"]]

    out = [
        "# Anomaly review",
        "",
        f"**{len(review)} nights need a decision** out of {len(history)} total "
        f"({len(review) / max(len(history), 1) * 100:.1f}%).",
        "",
        "For each night below: does it look like real (if unusual) sleep, or a "
        "data error? When in doubt, exclude — excluding is reversible, and the "
        "metrics recompute from full history every run.",
        "",
        "## How to action this",
        "",
        "1. Copy the date list at the bottom.",
        "2. Delete any dates you want to **keep**.",
        "3. Go to **Actions → Exclude a day → Run workflow**, paste the "
        "remaining dates into `date`, set a reason, and run.",
        "",
    ]

    if dropped:
        out += ["## Values auto-nulled as physiologically impossible", "",
                "These individual readings were dropped; the rest of each night "
                "is intact and needs no decision from you.", ""]
        for col, n in sorted(dropped.items(), key=lambda kv: -kv[1]):
            out.append(f"- `{col}`: {n} value(s)")
        out.append("")

    if starts:
        out += ["## Auto-detected reliable start dates", "",
                "These metrics had an unreliable early era, detected from the "
                "data rather than hardcoded. Earlier values are excluded from "
                "that metric only.", ""]
        for col, start in starts.items():
            out.append(f"- `{col}`: usable from **{start}**")
        out.append("")

    if not benign.empty:
        out += [
            f"## Explained automatically — no action needed ({len(benign)})",
            "",
            "Daylight-saving clock changes. The recorded duration is correct; "
            "only the bedtime→waketime subtraction is distorted, so these "
            "nights are kept and only their `timing` component is skipped.",
            "",
        ]
        out += [f"- {r['day']}" for _, r in benign.iterrows()]
        out.append("")

    out += [f"## Needs your decision ({len(review)})", ""]
    for reason_group, group in review.groupby("reasons", sort=False):
        out += [f"### {reason_group}", ""]
        for _, row in group.iterrows():
            out += [f"**{row['day']}**", "", context_table(history, row["day"]), ""]

    out += [
        "---",
        "",
        "## Date list to copy",
        "",
        "Delete any you want to keep, then paste the rest into the "
        "**Exclude a day** workflow:",
        "",
        "```",
        ",".join(str(d) for d in review["day"]),
        "```",
    ]
    return "\n".join(out)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config.ensure_dirs()

    history = store.load_history(config.HISTORY_PATH)
    if history.empty:
        print("No history found — run the Backfill workflow first.")
        return

    cleaned, dropped = quality.clamp_implausible(history)
    cleaned, starts = quality.apply_reliable_starts(cleaned, ["steps"])
    anomalies = quality.find_anomalies(cleaned)

    already = set(store.load_exclusions(config.EXCLUSIONS_PATH)["day"])
    if already:
        anomalies = anomalies[~anomalies["day"].isin(already)].reset_index(drop=True)

    report_path = config.DOCS_DIR / "review" / "anomalies.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        build_markdown(anomalies, cleaned, starts, dropped), encoding="utf-8"
    )

    csv_path = config.DATA_DIR / "anomalies.csv"
    anomalies.to_csv(csv_path, index=False)

    review = anomalies[~anomalies["benign"]] if not anomalies.empty else anomalies
    print(f"{len(review)} night(s) need review, "
          f"{len(anomalies) - len(review)} explained automatically.")
    print(f"Report: {report_path}")
    if already:
        print(f"({len(already)} already-excluded day(s) omitted.)")


if __name__ == "__main__":
    main()
