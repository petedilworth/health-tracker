"""Configuration, secrets and project paths.

Secrets come from environment variables. Locally those are populated from a
`.env` file (git-ignored); in GitHub Actions they come from repository secrets.
Either way the code reads them identically via os.environ.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # no-op in Actions, where there is no .env

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
IMAGES_DIR = PROJECT_ROOT / "images"
DOCS_DIR = PROJECT_ROOT / "docs"

HISTORY_PATH = DATA_DIR / "history.csv"
EXCLUSIONS_PATH = DATA_DIR / "exclusions.csv"
DASHBOARD_PATH = IMAGES_DIR / "dashboard.png"

DEFAULT_MAIL_FROM = "onboarding@resend.dev"

# --- your sleep opportunity target ------------------------------------------
# "Opportunity" is time in bed, the one part of sleep you actually control and
# the part the ring measures best (bedtime and waketime are the high-confidence
# tier). Edit these; nothing derives them from your history on purpose.
#
# Set 2026-09 from a stated preference: 8h in bed, lights out 22:15, up 06:15.
# Deliberately below the 8.52h that full sleep need would imply at your 87%
# efficiency, because a target met on 39% of nights is a game you can win and
# one met on 19% is not. See the sleep-opportunity page for the gap this leaves.
TARGET_TIB_H = 8.0
TARGET_WAKE_WEEKDAY_H = 6.25   # 06:15, as a decimal hour
TARGET_WAKE_WEEKEND_H = None   # None -> use your own trailing weekend median


def _get(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Set it in your .env (local) or as a GitHub Actions secret."
        )
    return value


@dataclass(frozen=True)
class Settings:
    oura_pat: str
    verify_tls: bool
    # Email is optional so the pipeline can still pull and commit data when the
    # mail secrets aren't configured.
    mail_to: str | None
    mail_from: str
    resend_api_key: str | None

    @property
    def email_enabled(self) -> bool:
        return bool(self.mail_to and self.resend_api_key)


def load_settings(require_oura: bool = True) -> Settings:
    verify_raw = (_get("OURA_VERIFY_TLS", "true") or "true").strip().lower()
    return Settings(
        oura_pat=_get("OURA_PAT", required=require_oura) or "",
        verify_tls=verify_raw not in ("false", "0", "no"),
        mail_to=_get("MAIL_TO"),
        mail_from=_get("MAIL_FROM", DEFAULT_MAIL_FROM) or DEFAULT_MAIL_FROM,
        resend_api_key=_get("RESEND_API_KEY"),
    )


def ensure_dirs() -> None:
    for d in (DATA_DIR, IMAGES_DIR, DOCS_DIR):
        d.mkdir(parents=True, exist_ok=True)
