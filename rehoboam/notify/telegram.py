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
    try:
        resp = requests.post(_API.format(token=token), json=payload, timeout=timeout)
    except Exception as exc:
        # NEVER log the exception object or a traceback here: requests embeds the
        # full request URL in its message, and that URL contains the bot token.
        logger.warning(
            "telegram: send failed for proposal %s (%s)",
            proposal_id,
            type(exc).__name__,
        )
        return False

    if resp.status_code != 200:
        logger.warning("telegram: send returned %s for proposal %s", resp.status_code, proposal_id)
        return False
    return True
