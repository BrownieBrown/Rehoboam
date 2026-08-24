"""Telegram delivery for trade proposals.

Best-effort by construction: every failure path returns False rather than
raising, because a notification outage must never stop the bot from setting a
lineup. Same contract as the project's learning-side persistence.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"

# Telegram rejects anything longer than this in a single message.
MAX_MESSAGE_CHARS = 4096


def _post(token: str, payload: dict, what: str, timeout: float) -> bool:
    """One sendMessage call. False on any failure — never raises."""
    try:
        resp = requests.post(_API.format(token=token), json=payload, timeout=timeout)
    except Exception as exc:
        # NEVER log the exception object or a traceback here: requests embeds the
        # full request URL in its message, and that URL contains the bot token.
        logger.warning("telegram: send failed for %s (%s)", what, type(exc).__name__)
        return False

    if resp.status_code != 200:
        logger.warning("telegram: send returned %s for %s", resp.status_code, what)
        return False
    return True


def _chunks(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split on line boundaries so a figure is never torn in half."""
    out: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            out.append(current)
            current = ""
        # A single line longer than the cap has to be cut somewhere.
        while len(line) > limit:
            out.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        out.append(current)
    return out


def send_message(token: str, chat_id: str, text: str, *, timeout: float = 10.0) -> bool:
    """Send plain text with no buttons. True only if every part was accepted.

    Carries the daily summary. That started as an SMTP email, but Proton needs
    a paid plan plus a custom domain, and Proton Bridge binds to localhost so
    an Azure Function can never reach it — the summary reuses the channel that
    is already configured and verified.
    """
    if not token or not chat_id:
        logger.info("telegram: no token or chat id configured — not sending")
        return False

    ok = True
    for part in _chunks(text):
        ok = _post(token, {"chat_id": chat_id, "text": part}, "daily summary", timeout) and ok
    return ok


def send_proposal(
    token: str,
    chat_id: str,
    proposal_id: str,
    text: str,
    *,
    timeout: float = 10.0,
) -> bool:
    """Send a proposal with Approve / Reject buttons. True if Telegram took it.

    The buttons carry ``approve:<id>`` / ``reject:<id>`` as callback data, which
    is what the webhook parses. The id is what makes the callback idempotent.
    """
    if not token or not chat_id:
        logger.info("telegram: no token or chat id configured — not sending")
        return False

    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve:{proposal_id}"},
                    {"text": "❌ Reject", "callback_data": f"reject:{proposal_id}"},
                ]
            ]
        },
    }
    return _post(token, payload, f"proposal {proposal_id}", timeout)
