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

# Most Approve/Reject rows to put on one summary. Nothing reaps a stale
# proposal yet (REH-108), so the pending queue grows without bound; a wall of
# buttons is unusable and risks Telegram refusing the markup outright.
MAX_APPROVAL_BUTTONS = 8


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


def _approval_row(proposal_id: str, approve_label: str, reject_label: str) -> list[dict]:
    """The one place the callback-data format is defined.

    ``approve:<id>`` / ``reject:<id>`` is what `notify.approval.handle_callback`
    parses. The id is what makes a callback idempotent — the handler claims the
    proposal before doing anything expensive, so a replay is safe.
    """
    return [
        {"text": approve_label, "callback_data": f"approve:{proposal_id}"},
        {"text": reject_label, "callback_data": f"reject:{proposal_id}"},
    ]


def approval_keyboard(approvals: list[tuple[str, str]]) -> dict:
    """One Approve/Reject row per pending proposal, labelled by player."""
    return {
        "inline_keyboard": [
            _approval_row(pid, f"\u2705 {name}", f"\u274c {name}")
            # Keep the TAIL, not the head. `pending_proposals()` is
            # oldest-first, and measured poach latency is hours (Ndiaye 2h48m,
            # Awortwie-Grant 8h30m on 2026-08-26), so an old pending proposal
            # is almost certainly dead while a fresh one is still winnable.
            for pid, name in approvals[-MAX_APPROVAL_BUTTONS:]
        ]
    }


def _send_chunked(
    token: str,
    chat_id: str,
    text: str,
    *,
    keyboard: dict | None,
    what: str,
    timeout: float,
) -> bool:
    """Send `text` in Telegram-sized parts, `keyboard` on the last part only.

    Both senders go through here. `send_proposal` used to post in one shot, so
    a proposal over the cap was refused with a 400 and never arrived — the
    decision existed only as a `trade_proposals` row nobody could see.

    Last chunk only: a keyboard repeated on every part makes one proposal
    tappable from several messages, and every tap after the first returns
    "already <status>", which reads as a broken button.
    """
    parts = _chunks(text)
    ok = True
    for i, part in enumerate(parts):
        payload: dict = {"chat_id": chat_id, "text": part}
        if keyboard and i == len(parts) - 1:
            payload["reply_markup"] = keyboard
        ok = _post(token, payload, what, timeout) and ok
    return ok


def send_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    approvals: list[tuple[str, str]] | None = None,
    timeout: float = 10.0,
) -> bool:
    """Send text, optionally with Approve/Reject buttons. True if all accepted.

    Carries the daily summary. That started as an SMTP email, but Proton needs
    a paid plan plus a custom domain, and Proton Bridge binds to localhost so
    an Azure Function can never reach it — the summary reuses the channel that
    is already configured and verified.

    ``approvals`` is ``[(proposal_id, player_name)]``, oldest first. REH-106:
    without it the summary named what was awaiting approval and offered no way
    to approve it, so proposals sat at `pending` until a rival bought the
    player — indistinguishable, from the reader's side, from a dead webhook.
    """
    if not token or not chat_id:
        logger.info("telegram: no token or chat id configured — not sending")
        return False

    return _send_chunked(
        token,
        chat_id,
        text,
        keyboard=approval_keyboard(approvals) if approvals else None,
        what="daily summary",
        timeout=timeout,
    )


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

    The player's name is in the rendered text directly above, so the buttons
    are labelled by action rather than by name — unlike the summary keyboard,
    which lists several proposals at once and needs the name to disambiguate.
    """
    if not token or not chat_id:
        logger.info("telegram: no token or chat id configured — not sending")
        return False

    return _send_chunked(
        token,
        chat_id,
        text,
        keyboard={"inline_keyboard": [_approval_row(proposal_id, "✅ Approve", "❌ Reject")]},
        what=f"proposal {proposal_id}",
        timeout=timeout,
    )
