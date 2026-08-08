"""Central configuration and secret loading.

Secrets come from environment variables. Locally those are populated from a
`.env` file (git-ignored); in GitHub Actions they come from repository secrets.
Either way the code reads them the same way via os.environ.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env if present. In Actions there is no .env and this is a harmless no-op.
load_dotenv()

# Project paths, resolved relative to this file so they work from any CWD.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
IMAGES_DIR = PROJECT_ROOT / "images"
CSV_PATH = DATA_DIR / "sleep_data_processed.csv"
DASHBOARD_PATH = IMAGES_DIR / "dashboard.png"


def _get(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Set it in your .env (local) or as a GitHub Actions secret."
        )
    return value


DEFAULT_MAIL_FROM = "onboarding@resend.dev"


@dataclass(frozen=True)
class Settings:
    oura_pat: str
    verify_tls: bool
    # Email is optional so the pipeline can run (and commit) even if email
    # secrets aren't configured yet.
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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
