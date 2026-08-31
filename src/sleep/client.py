"""Oura API v2 client.

Authenticates with a Personal Access Token (a single long-lived bearer token),
so there is no OAuth2 authorize/refresh flow to maintain for unattended runs.

Four endpoints are used:
  sleep            detailed sleep sessions — stages, HR, HRV, breathing, bedtimes
  daily_sleep      Oura's own sleep score and contributor breakdown
  daily_activity   steps
  daily_readiness  body-temperature deviation, Oura's readiness score
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterator

import requests

API_BASE = "https://api.ouraring.com/v2/usercollection"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 4

log = logging.getLogger("sleep.client")


class OuraClient:
    def __init__(self, access_token: str, verify_tls: bool = True):
        if not access_token:
            raise ValueError("An Oura access token (PAT) is required.")
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {access_token}"})
        self._verify = verify_tls

    def _get(self, url: str, params: dict[str, Any]) -> dict:
        """GET with a short retry on transient failures (5xx / network)."""
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._session.get(
                    url, params=params, verify=self._verify, timeout=REQUEST_TIMEOUT
                )
                # 4xx are permanent (bad token, bad range) — fail immediately so
                # the error is visible rather than retried into a timeout.
                if 400 <= resp.status_code < 500:
                    resp.raise_for_status()
                resp.raise_for_status()
                return resp.json()
            except (requests.HTTPError, requests.RequestException) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500:
                    raise
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    wait = 2 ** (attempt + 1)
                    log.warning("Request failed (%s); retrying in %ss", exc, wait)
                    import time

                    time.sleep(wait)
        raise RuntimeError(f"Oura request failed after {MAX_RETRIES} attempts") from last_error

    def _paginate(self, endpoint: str, start: dt.date, end: dt.date) -> Iterator[dict]:
        """Yield every record in a date range, following next_token pages."""
        url = f"{API_BASE}/{endpoint}"
        params = {"start_date": start.isoformat(), "end_date": end.isoformat()}
        next_token: str | None = None
        while True:
            page_params = dict(params)
            if next_token:
                page_params["next_token"] = next_token
            payload = self._get(url, page_params)
            yield from payload.get("data", [])
            next_token = payload.get("next_token")
            if not next_token:
                break

    def sleep_sessions(self, start: dt.date, end: dt.date) -> list[dict]:
        return list(self._paginate("sleep", start, end))

    def daily_sleep(self, start: dt.date, end: dt.date) -> list[dict]:
        return list(self._paginate("daily_sleep", start, end))

    def daily_activity(self, start: dt.date, end: dt.date) -> list[dict]:
        return list(self._paginate("daily_activity", start, end))

    def daily_readiness(self, start: dt.date, end: dt.date) -> list[dict]:
        return list(self._paginate("daily_readiness", start, end))

    def fetch_all(self, start: dt.date, end: dt.date) -> dict[str, list[dict]]:
        """Pull every endpoint for a range, returned keyed by endpoint name."""
        return {
            "sleep": self.sleep_sessions(start, end),
            "daily_sleep": self.daily_sleep(start, end),
            "daily_activity": self.daily_activity(start, end),
            "daily_readiness": self.daily_readiness(start, end),
        }
