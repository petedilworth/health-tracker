"""Orchestrate one daily run: pull -> upsert history -> compute -> render -> email.

The daily job pulls only a short recent window (the last N days) and upserts it
into the stored history CSV. Rolling averages and percentiles are then computed
over the *full* history so they're always correct, and the dashboard is rendered
and emailed.
"""

from __future__ import annotations

import datetime as dt
import logging

from . import config, processing, storage, viz
from .client import OuraClient
from .report_email import send_dashboard_email

log = logging.getLogger("oura.pipeline")

# How many days back to re-pull each run. Generous enough to catch late-synced
# nights or gaps, small enough to stay fast. Upsert makes overlap harmless.
LOOKBACK_DAYS = 30


def run(lookback_days: int = LOOKBACK_DAYS, send_email: bool = True) -> None:
    settings = config.load_settings(require_oura=True)
    config.ensure_dirs()

    end = dt.date.today()
    start = end - dt.timedelta(days=lookback_days)
    log.info("Pulling Oura data %s -> %s", start, end)

    client = OuraClient(settings.oura_pat, verify_tls=settings.verify_tls)
    daily_sleep = client.daily_sleep(start, end)
    sleep = client.sleep_sessions(start, end)
    log.info("Fetched %d daily_sleep, %d sleep sessions",
             len(daily_sleep), len(sleep))

    new_raw = processing.build_raw_frame(daily_sleep, sleep)

    history = storage.load_history(config.CSV_PATH)
    combined = storage.upsert(history, new_raw)
    storage.save_history(combined, config.CSV_PATH)
    log.info("History now holds %d nights (was %d)", len(combined), len(history))

    if combined.empty:
        log.warning("No data available; skipping dashboard/email.")
        return

    full = processing.add_rolling_and_percentiles(combined)
    viz.save_dashboard(full, config.DASHBOARD_PATH)
    log.info("Dashboard saved -> %s", config.DASHBOARD_PATH)

    if send_email:
        if settings.email_enabled:
            send_dashboard_email(settings, full, config.DASHBOARD_PATH)
            log.info("Email sent to %s", settings.mail_to)
        else:
            log.warning("Email secrets not set; skipping email step.")
