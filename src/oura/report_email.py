"""Email the dashboard via Resend, with the image embedded in the body.

Uses a single Resend API key (no SMTP, no app-password flow). The dashboard
PNG is sent as a base64 attachment with a content_id and referenced from the
HTML body via `cid:`, so it renders inline rather than as a download.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pandas as pd
import resend

from .config import Settings
from .viz import METRIC_CONFIG, _fmt_value


def _summary_html(df: pd.DataFrame) -> str:
    """A short text summary above the embedded dashboard image."""
    latest = df.iloc[-1]
    lines = []
    for metric, cfg in METRIC_CONFIG.items():
        if metric not in df.columns:
            continue
        val = _fmt_value(metric, latest[metric])
        pct = latest[f"{metric}_pct_daily"]
        lines.append(
            f"<li><b>{cfg['label']}:</b> {val} "
            f"({pct:.0f}th percentile vs. all history)</li>"
        )
    return "<ul>" + "".join(lines) + "</ul>"


def send_dashboard_email(settings: Settings, df: pd.DataFrame,
                         image_path: Path) -> None:
    if not settings.email_enabled:
        raise RuntimeError(
            "Email not configured (RESEND_API_KEY / MAIL_TO)."
        )

    latest_day = pd.to_datetime(df["day"].iloc[-1]).strftime("%A, %d %b %Y")
    html = (
        f"<h2>Oura Sleep Analytics</h2>"
        f"<p>{latest_day}</p>"
        f"{_summary_html(df)}"
        f'<img src="cid:dashboard" style="max-width:100%;">'
    )

    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("ascii")

    resend.api_key = settings.resend_api_key
    resend.Emails.send({
        "from": settings.mail_from,
        "to": settings.mail_to,
        "subject": f"Oura Sleep Analytics — {latest_day}",
        "html": html,
        "attachments": [{
            "filename": "dashboard.png",
            "content": image_b64,
            "content_id": "dashboard",
        }],
    })
