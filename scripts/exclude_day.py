#!/usr/bin/env python3
"""Exclude (or restore) one or many days.

Normally run from GitHub Actions ("Exclude a day"), usable from the mobile app:

    Actions -> Exclude a day -> Run workflow -> dates, reason, action

Accepts a single date or a comma/space/newline-separated list, so the whole
anomaly review can be actioned in one run.

    python scripts/exclude_day.py --date 2026-08-11 --reason "not charged"
    python scripts/exclude_day.py --date "2019-03-04,2019-07-22" --reason "travel"
    python scripts/exclude_day.py --date 2026-08-11 --action include
"""

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import config, store  # noqa: E402


def parse_dates(raw: str) -> list[dt.date]:
    """Split on commas/whitespace/newlines and parse each as a date."""
    tokens = [t for t in re.split(r"[,\s]+", raw.strip()) if t]
    if not tokens:
        raise ValueError("No dates given.")
    days, bad = [], []
    for token in tokens:
        try:
            days.append(dt.date.fromisoformat(token))
        except ValueError:
            bad.append(token)
    if bad:
        raise ValueError(f"Not valid YYYY-MM-DD dates: {', '.join(bad)}")
    # De-duplicate, preserving order.
    return list(dict.fromkeys(days))


def main() -> None:
    parser = argparse.ArgumentParser(description="Exclude or restore days.")
    parser.add_argument("--date", required=True,
                        help="Day(s) to act on: YYYY-MM-DD, comma-separated for many.")
    parser.add_argument("--reason", default="", help="Why it's being excluded.")
    parser.add_argument("--action", choices=["exclude", "include"],
                        default="exclude",
                        help="exclude removes the day(s); include restores them.")
    args = parser.parse_args()

    days = parse_dates(args.date)
    reason = args.reason.strip() or "unspecified"
    config.ensure_dirs()

    for day in days:
        if args.action == "exclude":
            rows = store.add_exclusion(config.EXCLUSIONS_PATH, day, reason)
        else:
            rows = store.remove_exclusion(config.EXCLUSIONS_PATH, day)

    verb = "Excluded" if args.action == "exclude" else "Restored"
    listed = ", ".join(str(d) for d in days[:10])
    if len(days) > 10:
        listed += f" (+{len(days) - 10} more)"
    print(f"{verb} {len(days)} day(s): {listed}")
    if args.action == "exclude":
        print(f"Reason: {reason}")
    print(f"{len(rows)} day(s) now excluded in total.")


if __name__ == "__main__":
    main()
