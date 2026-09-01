#!/usr/bin/env python3
"""Entry point for the daily run.

Current scope: pull recent Oura data, update history, and recompute all
metrics into data/computed.csv. The website (Stage 3) and email (Stage 4)
consume that file.

Usage:
    python run.py                  # pull, store, compute
    python run.py --lookback 90    # re-pull a longer recent window
    python run.py --compute-only   # recompute from stored history, no API call
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sleep import compute, ingest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily sleep analytics run.")
    parser.add_argument("--lookback", type=int, default=ingest.DEFAULT_LOOKBACK_DAYS,
                        help="Days of recent data to re-pull (default: 30).")
    parser.add_argument("--compute-only", action="store_true",
                        help="Skip the Oura pull; recompute from stored history.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.compute_only:
        ingest.ingest_recent(lookback_days=args.lookback)

    daily, summary = compute.compute()
    if daily.empty:
        logging.warning("No data to compute — run the Backfill workflow first.")
        return

    compute.save(daily)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
