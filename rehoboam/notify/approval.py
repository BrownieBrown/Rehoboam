"""Handling a Telegram approval callback.

This runs behind a public HTTP endpoint and can spend money, so the order of
operations matters and is deliberate:

    authenticate -> claim the proposal -> re-validate -> gate -> execute

Claiming before validating is what makes a replayed callback safe: the second
call finds the proposal already out of 'pending' and stops. Re-validating after
claiming is what makes a stale proposal safe: market values update daily after
10:00, so the numbers in the message are out of date by construction and must
never be the numbers we act on.

Everything from the claim onward runs inside one try/except: a failure partway
through (market fetch timeout, a locked db, anything) must still land the
proposal in 'failed' rather than stranding it in 'approved' — a stranded claim
would look like a lost callback to Telegram, which retries, re-claims (the
guard only blocks 'pending' -> X twice, not a stuck 'approved' row it can't
re-enter), and buys the same player twice.
"""

from __future__ import annotations

import hmac
import logging

from rehoboam.services.safety_gate import check_buy

logger = logging.getLogger(__name__)


def authorize(secret_header: str | None, expected: str) -> bool:
    """Constant-time check of Telegram's shared secret.

    Exposed separately from ``handle_callback`` so the HTTP trigger can reject
    an unauthenticated caller *before* spending a blob round trip and a
    Kickbase login on it. An unset ``expected`` rejects everything rather than
    accepting everything.
    """
    return bool(expected) and hmac.compare_digest(secret_header or "", expected)


def _record_bid_for_learning(learner, player, bid: int) -> None:
    """Feed an approved buy into the loop the autonomous path already uses.

    ``ExecutionService.buy`` records every autonomous bid as pending so
    ``resolve_auctions`` can later write an ``auction_outcomes`` row. Without
    the same call here, approved buys are invisible to the learned-overbid
    calibration — permanently, and silently, for exactly the buy class this
    branch routes through a human.

    Best-effort by construction: the purchase has already happened, so a
    learning failure must not change what we report.
    """
    try:
        from rehoboam.learning.tracker import LearningTracker

        LearningTracker(learner).record_bid_placed(player, bid)
    except Exception:
        logger.warning("approval: could not record approved bid for learning", exc_info=True)


def handle_callback(
    body: dict,
    secret_header: str | None,
    *,
    settings,
    learner,
    api,
    league,
) -> str:
    """Process one callback. Returns the text to show back in Telegram."""
    if not authorize(secret_header, settings.telegram_webhook_secret):
        logger.warning("approval: unauthorized callback rejected")
        return "Unauthorized."

    query = (body or {}).get("callback_query") or {}
    data = query.get("data") or ""
    action, _, proposal_id = data.partition(":")
    if action not in {"approve", "reject"} or not proposal_id:
        return "Unrecognised callback."

    proposal = learner.get_proposal(proposal_id)
    if proposal is None:
        return f"Proposal {proposal_id} not found."

    if action == "reject":
        if not learner.mark_proposal(proposal_id, "rejected"):
            return f"Proposal {proposal_id} was already {proposal['status']}."
        return f"Rejected {proposal['player_name']}."

    # Claim before doing anything expensive — this is the replay guard.
    if not learner.mark_proposal(proposal_id, "approved"):
        return f"Proposal {proposal_id} was already {proposal['status']}."

    try:
        market = {p.id: p for p in api.get_market(league)}
        live = market.get(proposal["player_id"])
        if live is None:
            learner.set_proposal_status(proposal_id, "failed")
            return f"{proposal['player_name']} is no longer on the market."

        squad = api.get_squad(league)
        bids = api.get_my_bids(league)

        # Kickbase's `buy_player` places an OFFER, and the reported budget does
        # not deduct open offers. The autonomous path knows this and subtracts
        # them (`_compute_flip_budget`); without the same subtraction here, two
        # proposals approved in one sitting each see the full budget, both
        # offers land, and the budget is negative at kickoff — which under the
        # league's rules is zero points for the entire matchday.
        pending_bid_total = sum(int(getattr(b, "user_offer_price", 0) or 0) for b in bids)
        budget = int(api.get_team_info(league).get("budget", 0)) - pending_bid_total
        free_slots = 15 - len(squad) - len(bids)

        # League rule: max three players per club. Counted from the live squad
        # plus open offers, because a pending bid will occupy a slot too.
        club_counts: dict[str, int] = {}
        for held in list(squad) + list(bids):
            cid = str(getattr(held, "team_id", "") or "")
            if cid:
                club_counts[cid] = club_counts.get(cid, 0) + 1

        result = check_buy(
            player_id=proposal["player_id"],
            bid=int(proposal["bid"]),
            market_value=int(live.market_value),
            current_budget=budget,
            free_slots=free_slots,
            known_player_ids=market.keys(),
            max_overbid_pct=settings.max_overbid_pct,
            club_id=str(getattr(live, "team_id", "") or "") or None,
            squad_club_counts=club_counts,
        )
        if not result.ok:
            learner.set_proposal_status(proposal_id, "failed")
            return "Not executed:\n" + "\n".join(f"- {r}" for r in result.reasons)

        api.buy_player(league, live, int(proposal["bid"]))
    except Exception as exc:
        learner.set_proposal_status(proposal_id, "failed")
        logger.exception("approval: could not execute %s", proposal_id)
        return f"Buy failed: {exc}"

    learner.set_proposal_status(proposal_id, "executed")
    _record_bid_for_learning(learner, live, int(proposal["bid"]))
    return f"Bought {proposal['player_name']} for EUR {int(proposal['bid']):,}."


def build_callback_response(body: dict, reply: str) -> dict:
    """The webhook body that makes Telegram show `reply` to the tapper.

    Telegram only acts on a webhook response that names a method; a bare
    {"text": ...} is silently ignored and the button spins forever.
    `answerCallbackQuery` caps text at 200 characters.
    """
    query = (body or {}).get("callback_query") or {}
    return {
        "method": "answerCallbackQuery",
        "callback_query_id": query.get("id", ""),
        "text": reply[:200],
        "show_alert": True,
    }
