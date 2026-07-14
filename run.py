#!/usr/bin/env python3
"""Entry point for the daily Oura analytics run.

Usage:
    python run.py                 # pull, update history, render, email
    python run.py --no-email      # everything except sending the email
    python run.py --lookback 60   # re-pull a longer recent window
"""

import argparse
import logging
import sys
from pathlib import Path

# Make the src/ package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from oura import pipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Oura sleep analytics run.")
    parser.add_argument("--no-email", action="store_true",
                        help="Skip sending the email.")
    parser.add_argument("--lookback", type=int, default=pipeline.LOOKBACK_DAYS,
                        help="Days of recent data to re-pull (default: 30).")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    pipeline.run(lookback_days=args.lookback, send_email=not args.no_email)


if __name__ == "__main__":
    main()
