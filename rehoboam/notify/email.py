"""SMTP delivery for the daily summary. Best-effort, like Telegram."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def send_email(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    timeout: float = 20.0,
) -> bool:
    """Send one plain-text email. False on any failure — never raises."""
    if not (host and sender and recipient):
        logger.info("email: not configured — not sending")
        return False

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)
    except Exception:
        logger.warning("email: send failed", exc_info=True)
        return False
    return True
