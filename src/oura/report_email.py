"""Email the dashboard via Gmail SMTP with the image embedded in the body.

Uses an app password (not the account password) over SMTP-SSL. The dashboard
PNG is attached inline via a Content-ID so it renders in the email body rather
than as a download.
"""

from __future__ import annotations

import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd

from .config import Settings
from .viz import METRIC_CONFIG, _fmt_value

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SSL


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
            "Email secrets not configured (MAIL_TO / GMAIL_USER / "
            "GMAIL_APP_PASSWORD)."
        )

    latest_day = pd.to_datetime(df["day"].iloc[-1]).strftime("%A, %d %b %Y")
    msg = MIMEMultipart("related")
    msg["Subject"] = f"Oura Sleep Analytics — {latest_day}"
    msg["From"] = settings.gmail_user
    msg["To"] = settings.mail_to

    body = (
        f"<h2>Oura Sleep Analytics</h2>"
        f"<p>{latest_day}</p>"
        f"{_summary_html(df)}"
        f'<img src="cid:dashboard" style="max-width:100%;">'
    )
    msg.attach(MIMEText(body, "html"))

    with open(image_path, "rb") as f:
        img = MIMEImage(f.read())
    img.add_header("Content-ID", "<dashboard>")
    img.add_header("Content-Disposition", "inline", filename="dashboard.png")
    msg.attach(img)

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(settings.gmail_user, settings.gmail_app_password)
        server.send_message(msg)
