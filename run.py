#!/usr/bin/env python3
"""Entry point for the daily run.

Stage 1 scope: pull recent Oura data and update `data/history.csv`.
Metrics, website and email are added in later stages.

Usage:
    python run.py                  # pull the last 30 days, update history
    python run.py --lookback 90    # re-pull a longer recent window
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sleep import ingest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily sleep analytics run.")
    parser.add_argument("--lookback", type=int, default=ingest.DEFAULT_LOOKBACK_DAYS,
                        help="Days of recent data to re-pull (default: 30).")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ingest.ingest_recent(lookback_days=args.lookback)


if __name__ == "__main__":
    main()
