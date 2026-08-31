#!/usr/bin/env python3
"""Exclude (or un-exclude) a day from all metrics.

Normally run from GitHub Actions ("Exclude a day" workflow), which is usable
from the GitHub mobile app:

    Actions -> Exclude a day -> Run workflow -> date, reason, action

Locally:

    python scripts/exclude_day.py --date 2026-08-11 --reason "ring not charged"
    python scripts/exclude_day.py --date 2026-08-11 --action include
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import config, store  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Exclude or restore a day.")
    parser.add_argument("--date", required=True, help="Day to act on (YYYY-MM-DD).")
    parser.add_argument("--reason", default="", help="Why it's being excluded.")
    parser.add_argument("--action", choices=["exclude", "include"],
                        default="exclude",
                        help="exclude removes the day; include restores it.")
    args = parser.parse_args()

    day = dt.date.fromisoformat(args.date.strip())
    config.ensure_dirs()

    if args.action == "exclude":
        rows = store.add_exclusion(config.EXCLUSIONS_PATH, day, args.reason.strip())
        print(f"Excluded {day} ({args.reason.strip() or 'unspecified'}). "
              f"{len(rows)} day(s) now excluded.")
    else:
        rows = store.remove_exclusion(config.EXCLUSIONS_PATH, day)
        print(f"Restored {day}. {len(rows)} day(s) still excluded.")


if __name__ == "__main__":
    main()
