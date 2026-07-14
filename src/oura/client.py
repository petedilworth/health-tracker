"""Thin client for the Oura API v2.

Uses a Personal Access Token (PAT) — a single long-lived bearer token — so
there is no OAuth2 authorize/refresh flow to manage for unattended runs.

Two endpoints are used:
  - daily_sleep : the nightly sleep *score* (0-100) and contributor breakdown
  - sleep       : detailed sleep sessions, including bedtime_start / bedtime_end
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterator

import requests

API_BASE = "https://api.ouraring.com/v2/usercollection"
# Oura paginates long date ranges; a page holds up to ~this many rows.
REQUEST_TIMEOUT = 30


class OuraClient:
    def __init__(self, access_token: str, verify_tls: bool = True):
        if not access_token:
            raise ValueError("An Oura access token (PAT) is required.")
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {access_token}"})
        self._verify = verify_tls

    def _paginate(self, endpoint: str, params: dict[str, Any]) -> Iterator[dict]:
        """Yield every record for a date range, following next_token pages."""
        url = f"{API_BASE}/{endpoint}"
        next_token: str | None = None
        while True:
            page_params = dict(params)
            if next_token:
                page_params["next_token"] = next_token
            resp = self._session.get(
                url, params=page_params, verify=self._verify, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            payload = resp.json()
            yield from payload.get("data", [])
            next_token = payload.get("next_token")
            if not next_token:
                break

    def daily_sleep(self, start: dt.date, end: dt.date) -> list[dict]:
        """Nightly sleep score records between start and end (inclusive)."""
        return list(
            self._paginate(
                "daily_sleep",
                {"start_date": start.isoformat(), "end_date": end.isoformat()},
            )
        )

    def sleep_sessions(self, start: dt.date, end: dt.date) -> list[dict]:
        """Detailed sleep sessions (naps + long sleeps) for the range."""
        return list(
            self._paginate(
                "sleep",
                {"start_date": start.isoformat(), "end_date": end.isoformat()},
            )
        )
