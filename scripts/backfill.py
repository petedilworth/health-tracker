#!/usr/bin/env python3
"""Full-history backfill.

Normally run from GitHub Actions (the "Backfill history" workflow) so no local
Python setup is needed:

    Actions -> Backfill history -> Run workflow -> set start/end dates

Locally it works the same way:

    python scripts/backfill.py --start 2019-01-01
"""

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sleep import ingest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill full Oura history.")
    parser.add_argument("--start", default="2019-01-01",
                        help="Start date YYYY-MM-DD (default 2019-01-01).")
    parser.add_argument("--end", default=dt.date.today().isoformat(),
                        help="End date YYYY-MM-DD (default today).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    history = ingest.ingest_range(
        dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    )
    print(f"Backfill complete: {len(history)} nights stored.")


if __name__ == "__main__":
    main()
