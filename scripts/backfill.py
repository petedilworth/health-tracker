#!/usr/bin/env python3
"""One-time historical backfill.

Pulls your entire Oura history (default: from 2019-01-01 to today) and writes it
to the history CSV. Run this once locally to seed the data the daily job then
maintains incrementally.

    python scripts/backfill.py                 # from 2019-01-01
    python scripts/backfill.py --start 2020-06-01

Oura's API caps each request's date span, so this walks the range in chunks.
"""

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oura import config, processing, storage  # noqa: E402
from oura.client import OuraClient  # noqa: E402

log = logging.getLogger("oura.backfill")

CHUNK_DAYS = 180  # stay well under Oura's per-request span limit


def daterange_chunks(start: dt.date, end: dt.date, size: int):
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=size - 1), end)
        yield cur, chunk_end
        cur = chunk_end + dt.timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill full Oura history.")
    parser.add_argument("--start", default="2019-01-01",
                        help="Start date YYYY-MM-DD (default 2019-01-01).")
    parser.add_argument("--end", default=dt.date.today().isoformat(),
                        help="End date YYYY-MM-DD (default today).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)

    settings = config.load_settings(require_oura=True)
    config.ensure_dirs()
    client = OuraClient(settings.oura_pat, verify_tls=settings.verify_tls)

    history = storage.load_history(config.CSV_PATH)
    for c_start, c_end in daterange_chunks(start, end, CHUNK_DAYS):
        log.info("Pulling %s -> %s", c_start, c_end)
        daily_sleep = client.daily_sleep(c_start, c_end)
        sleep = client.sleep_sessions(c_start, c_end)
        chunk = processing.build_raw_frame(daily_sleep, sleep)
        history = storage.upsert(history, chunk)

    storage.save_history(history, config.CSV_PATH)
    log.info("Backfill complete: %d nights -> %s", len(history), config.CSV_PATH)


if __name__ == "__main__":
    main()
