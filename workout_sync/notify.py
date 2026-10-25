"""Failure alerts by email (optional: only when SMTP_HOST and ALERT_TO are set)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import Settings

log = logging.getLogger(__name__)


def send_alert(settings: Settings, subject: str, body: str) -> None:
    if not (settings.smtp_host and settings.alert_to):
        return
    msg = EmailMessage()
    msg["Subject"] = f"[workout-sync] {subject}"
    msg["From"] = settings.smtp_user or settings.alert_to
    msg["To"] = settings.alert_to
    msg.set_content(body + f"\n\n{settings.public_url}\n")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
            s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    except Exception:
        log.exception("Could not send alert email")
