# Approval-Gated Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The bot sets lineups alone; squad-improving trades are proposed to Telegram with their full reasoning and execute on one tap; a daily email reports state.

**Architecture:** Four pure units (safety gate, proposal store, message renderer, email renderer) plus two IO shells (Telegram HTTP, SMTP) and one HTTP-triggered Azure Function for approvals. The trade phase stops executing improvement buys and records proposals instead. Nothing here touches scoring.

**Tech Stack:** Python 3.12, pytest, pydantic-settings, SQLite, `requests` 2.33.1 (already in the Function app), `smtplib` (stdlib), Azure Functions Python v2 programming model.

**Spec:** `docs/superpowers/specs/2026-08-23-approval-gated-trading-design.md`

## Global Constraints

- Every tunable is a `Settings` field, `.env`-tunable without a deploy. No inline magic numbers.
- Real Kickbase points everywhere; never reintroduce a 0-100 index constant.
- **Notification failures must never block trading or lineup setting.** Wrap every Telegram and SMTP call in `try/except`, log, and continue — the project's existing best-effort learning pattern.
- Proposals persist in `bid_learning.db`, not a JSON file. `pending_bids.json` and `tracked_purchases.json` were migrated into tables precisely because loose JSON is not blob-synced; `bid_learning.db` is in `azure_blob.DB_FILES`.
- Secrets are Key Vault references surfaced as app settings, as `KICKBASE_PASSWORD` already is. Never commit a token, chat id, or password.
- Run `uv run pytest -q -m "not slow"` before every commit. 887 tests pass at the start of this plan.
- `uv run ruff check rehoboam/ tests/` must pass.
- Do NOT run `black` manually — pre-commit pins black 25.1.0 at `--line-length=100`. If a commit fails because a hook reformatted a file, `git add` it again and re-run the same commit.

______________________________________________________________________

### Task 1: Safety gate

A pure function applied to anything that executes, autonomous or approved. This is where the risk lives, so it is built first and alone.

**Scope:** this gate covers the **buy** path, which is what the approval webhook exposes. The spec also lists lineup-side checks (eleven fieldable, legal formation, no injured player selected); those already exist and are already enforced — `formation.can_fill_starting_eleven` and `select_best_eleven` for legality, and the serving-time injury override shipped 2026-08-22 for availability. Do not reimplement them here; a second implementation of a rule the codebase already owns is the drift `scoring/v2/thresholds.py` forbids.

**Files:**

- Create: `rehoboam/services/safety_gate.py`
- Modify: `rehoboam/config.py` (add `max_overbid_pct`)
- Test: `tests/test_safety_gate.py`

**Interfaces:**

- Produces: `GateResult(ok: bool, reasons: list[str])` and
  `check_buy(*, player_id, bid, market_value, current_budget, free_slots, known_player_ids, max_overbid_pct) -> GateResult`

- [ ] **Step 1: Write the failing test**

Create `tests/test_safety_gate.py`:

```python
"""The safety gate — applied to everything that executes.

A public webhook will call this before spending money, so it is written and
tested before anything can reach it.
"""

import pytest

from rehoboam.services.safety_gate import GateResult, check_buy


def _ok_kwargs(**over):
    base = dict(
        player_id="6080",
        bid=32_608_485,
        market_value=32_285_629,
        current_budget=95_317_114,
        free_slots=2,
        known_player_ids={"6080", "859"},
        max_overbid_pct=8.0,
    )
    base.update(over)
    return base


class TestAcceptsAValidBuy:
    def test_a_normal_buy_passes(self):
        assert check_buy(**_ok_kwargs()).ok is True

    def test_a_passing_result_carries_no_reasons(self):
        assert check_buy(**_ok_kwargs()).reasons == []


class TestBudgetSafety:
    def test_a_buy_that_would_go_negative_is_refused(self):
        """Negative budget at kickoff scores ZERO for the whole matchday."""
        r = check_buy(**_ok_kwargs(current_budget=1_000_000))
        assert r.ok is False
        assert any("budget" in x.lower() for x in r.reasons)

    def test_spending_exactly_the_budget_is_allowed(self):
        r = check_buy(**_ok_kwargs(current_budget=32_608_485))
        assert r.ok is True


class TestOverbidCap:
    def test_a_bid_above_the_cap_is_refused(self):
        """32,285,629 * 1.08 = 34,868,479. A 38.3M bid is 18.7% over."""
        r = check_buy(**_ok_kwargs(bid=38_336_318))
        assert r.ok is False
        assert any("overbid" in x.lower() for x in r.reasons)

    def test_a_bid_at_exactly_the_cap_is_allowed(self):
        r = check_buy(**_ok_kwargs(bid=int(32_285_629 * 1.08)))
        assert r.ok is True


class TestSlots:
    def test_no_free_slot_is_refused(self):
        r = check_buy(**_ok_kwargs(free_slots=0))
        assert r.ok is False
        assert any("slot" in x.lower() for x in r.reasons)


class TestHallucinatedIdentifiers:
    def test_an_unknown_player_id_is_refused(self):
        """A model or a forged webhook can name a player we never sent."""
        r = check_buy(**_ok_kwargs(player_id="99999"))
        assert r.ok is False
        assert any("unknown player" in x.lower() for x in r.reasons)


class TestMultipleFailures:
    def test_all_failing_reasons_are_reported_not_just_the_first(self):
        r = check_buy(**_ok_kwargs(player_id="99999", free_slots=0, current_budget=1))
        assert r.ok is False
        assert len(r.reasons) >= 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_safety_gate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.services.safety_gate'`

- [ ] **Step 3: Write minimal implementation**

Create `rehoboam/services/safety_gate.py`:

```python
"""Hard limits applied to anything that executes, autonomous or approved.

This is the last thing between a decision and real money. It is a pure
function so it can be exhaustively tested, and it collects ALL failing
reasons rather than short-circuiting on the first — a caller reporting to
Telegram should be able to say everything that is wrong at once.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GateResult:
    """Outcome of a gate check. ``reasons`` is empty when ``ok`` is True."""

    ok: bool
    reasons: list[str] = field(default_factory=list)


def check_buy(
    *,
    player_id: str,
    bid: int,
    market_value: int,
    current_budget: int,
    free_slots: int,
    known_player_ids: Iterable[str],
    max_overbid_pct: float,
) -> GateResult:
    """Is this buy allowed to execute?

    ``known_player_ids`` is the set of ids from the data this decision was
    made against. An id outside it never reaches ``api.buy_player``: a forged
    webhook callback, or a stale proposal naming a player who has since left
    the market, must not be able to spend money.
    """
    reasons: list[str] = []

    if player_id not in set(known_player_ids):
        reasons.append(
            f"unknown player id {player_id!r} — not in the current market data"
        )

    if current_budget - bid < 0:
        reasons.append(
            f"budget would go negative: EUR {current_budget:,} - EUR {bid:,} "
            f"= EUR {current_budget - bid:,} (zero points for the matchday)"
        )

    if market_value > 0:
        cap = market_value * (1.0 + max_overbid_pct / 100.0)
        if bid > cap:
            over = (bid / market_value - 1.0) * 100.0
            reasons.append(
                f"overbid {over:.1f}% exceeds the {max_overbid_pct:.1f}% cap "
                f"(bid EUR {bid:,} vs market value EUR {market_value:,})"
            )

    if free_slots <= 0:
        reasons.append("no free squad slot")

    return GateResult(ok=not reasons, reasons=reasons)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_safety_gate.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: Add the Settings field**

In `rehoboam/config.py`, next to the other bidding knobs:

```python
max_overbid_pct: float = Field(
    default=8.0,
    description=(
        "Hard ceiling on how far above market value any bid may go, enforced by "
        "services/safety_gate.check_buy. 8.0 comes from REH-64's measurement: a "
        "3-8% overbid won 50% of auctions and an 8-15% overbid won the same 50%, "
        "so margin beyond ~8% bought almost no win rate while the bot averaged "
        "+12.2% and lost EUR 55.3M across 151 flips. Distinct from SmartBidding's "
        "own internal caps — this is the outer limit nothing may cross."
    ),
)
```

- [ ] **Step 6: Full suite and lint**

Run: `uv run pytest -q -m "not slow" && uv run ruff check rehoboam/ tests/`
Expected: 897 passed, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/services/safety_gate.py rehoboam/config.py tests/test_safety_gate.py
git commit -m "feat(safety): pure gate for anything that executes"
```

______________________________________________________________________

### Task 2: Proposal store

Proposals must survive between the run that creates one and the webhook that approves it, across two different Azure Function invocations and a blob round-trip.

**Files:**

- Modify: `rehoboam/bid_learner.py` (new table + four methods)
- Test: `tests/test_proposal_store.py`

**Interfaces:**

- Consumes: nothing from Task 1.

- Produces, all on `BidLearner`:
  `record_proposal(proposal_id: str, player_id: str, player_name: str, bid: int, market_value: int, message: str) -> None`;
  `get_proposal(proposal_id: str) -> dict | None`;
  `mark_proposal(proposal_id: str, status: str) -> bool` (pending-only, the replay lock);
  `set_proposal_status(proposal_id: str, status: str) -> None` (unguarded, for transitions AFTER claiming);
  `pending_proposals() -> list[dict]`.
  Rows carry `status` in `{"pending", "approved", "rejected", "expired", "executed", "failed"}` and a `created_at` float epoch.

- [ ] **Step 1: Write the failing test**

Create `tests/test_proposal_store.py`:

```python
"""Trade proposals persist in bid_learning.db, not a JSON file.

pending_bids.json and tracked_purchases.json were migrated into tables because
loose JSON is not synced to blob storage. A proposal is created by the timer
run and approved by a separate HTTP invocation, so it MUST survive the round
trip or approving does nothing.
"""

import time

import pytest

from rehoboam.bid_learner import BidLearner


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bids.db")


def _record(learner, pid="p1"):
    learner.record_proposal(
        proposal_id=pid,
        player_id="6080",
        player_name="Pavlović",
        bid=32_608_485,
        market_value=32_285_629,
        message="BUY Pavlović",
    )


class TestRoundTrip:
    def test_a_recorded_proposal_can_be_read_back(self, learner):
        _record(learner)
        p = learner.get_proposal("p1")
        assert p is not None
        assert p["player_id"] == "6080"
        assert p["bid"] == 32_608_485
        assert p["status"] == "pending"

    def test_an_unknown_id_returns_none(self, learner):
        assert learner.get_proposal("nope") is None

    def test_created_at_is_populated(self, learner):
        _record(learner)
        assert learner.get_proposal("p1")["created_at"] <= time.time()


class TestStatusTransitions:
    def test_marking_changes_the_status(self, learner):
        _record(learner)
        assert learner.mark_proposal("p1", "approved") is True
        assert learner.get_proposal("p1")["status"] == "approved"

    def test_marking_an_unknown_proposal_returns_false(self, learner):
        assert learner.mark_proposal("nope", "approved") is False


class TestIdempotency:
    def test_a_proposal_can_only_leave_pending_once(self, learner):
        """Telegram retries callbacks. A second tap must not re-approve."""
        _record(learner)
        assert learner.mark_proposal("p1", "approved") is True
        assert learner.mark_proposal("p1", "approved") is False
        assert learner.get_proposal("p1")["status"] == "approved"

    def test_a_rejected_proposal_cannot_later_be_approved(self, learner):
        _record(learner)
        learner.mark_proposal("p1", "rejected")
        assert learner.mark_proposal("p1", "approved") is False
        assert learner.get_proposal("p1")["status"] == "rejected"


class TestUnguardedTransition:
    def test_set_status_works_after_a_proposal_has_been_claimed(self, learner):
        """The webhook claims first, then reports the outcome."""
        _record(learner)
        learner.mark_proposal("p1", "approved")
        learner.set_proposal_status("p1", "executed")
        assert learner.get_proposal("p1")["status"] == "executed"


class TestListing:
    def test_pending_lists_only_pending(self, learner):
        _record(learner, "p1")
        _record(learner, "p2")
        learner.mark_proposal("p2", "approved")
        assert [p["proposal_id"] for p in learner.pending_proposals()] == ["p1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_proposal_store.py -q`
Expected: FAIL with `AttributeError: 'BidLearner' object has no attribute 'record_proposal'`

- [ ] **Step 3: Add the table**

In `rehoboam/bid_learner.py`, inside `_init_db`, alongside the other `CREATE TABLE IF NOT EXISTS` statements:

```python
conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    bid INTEGER NOT NULL,
                    market_value INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """)
```

- [ ] **Step 4: Add the four methods**

In `rehoboam/bid_learner.py`, as methods on `BidLearner`:

```python
def record_proposal(
    self,
    *,
    proposal_id: str,
    player_id: str,
    player_name: str,
    bid: int,
    market_value: int,
    message: str,
) -> None:
    """Persist a proposal awaiting approval."""
    with sqlite3.connect(self.db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO trade_proposals "
            "(proposal_id, player_id, player_name, bid, market_value, message, "
            " status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                proposal_id,
                player_id,
                player_name,
                int(bid),
                int(market_value),
                message,
                datetime.now().timestamp(),
            ),
        )


def get_proposal(self, proposal_id: str) -> dict | None:
    """One proposal by id, or None."""
    with sqlite3.connect(self.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM trade_proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
    return dict(row) if row else None


def mark_proposal(self, proposal_id: str, status: str) -> bool:
    """Move a proposal out of 'pending'. Returns False if it already left.

    The WHERE clause is the idempotency guarantee: Telegram retries
    callbacks, and a second tap must not buy the player twice.
    """
    with sqlite3.connect(self.db_path) as conn:
        cur = conn.execute(
            "UPDATE trade_proposals SET status = ? "
            "WHERE proposal_id = ? AND status = 'pending'",
            (status, proposal_id),
        )
        return cur.rowcount > 0


def set_proposal_status(self, proposal_id: str, status: str) -> None:
    """Set a status unconditionally.

    Distinct from ``mark_proposal``, which only moves a row OUT of
    'pending' and is the replay lock. Once a callback has claimed a
    proposal it owns it, and the follow-up transitions to 'executed' or
    'failed' must not be blocked by that guard.
    """
    with sqlite3.connect(self.db_path) as conn:
        conn.execute(
            "UPDATE trade_proposals SET status = ? WHERE proposal_id = ?",
            (status, proposal_id),
        )


def pending_proposals(self) -> list[dict]:
    """All proposals still awaiting a decision, oldest first."""
    with sqlite3.connect(self.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trade_proposals WHERE status = 'pending' "
            "ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_proposal_store.py -q`
Expected: PASS (9 passed)

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q -m "not slow" && uv run ruff check rehoboam/ tests/
git add rehoboam/bid_learner.py tests/test_proposal_store.py
git commit -m "feat(learning): persist trade proposals in bid_learning.db"
```

______________________________________________________________________

### Task 3: Proposal message renderer

The message Marco reads. Pure, so its exact content is testable.

**Files:**

- Create: `rehoboam/notify/__init__.py`, `rehoboam/notify/render.py`
- Test: `tests/test_proposal_render.py`

**Interfaces:**

- Produces: `render_proposal(*, player_name, club, bid, market_value, ep, displaced_name, displaced_ep, marginal_gain, budget_before, trend_7d_pct, risks: list[str]) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_proposal_render.py`:

```python
"""The proposal message.

Marco asked for three things explicitly: why this is a buy, why it improves the
lineup, and why this price. Each gets its own asserted section — a renderer
that silently drops one is the failure mode that matters.
"""

from rehoboam.notify.render import render_proposal


def _render(**over):
    base = dict(
        player_name="Aleksandar Pavlović",
        club="Bayern",
        bid=32_608_485,
        market_value=32_285_629,
        ep=82.6,
        displaced_name="Klaas",
        displaced_ep=25.5,
        marginal_gain=57.2,
        budget_before=95_317_114,
        trend_7d_pct=1.9,
        risks=["Availability is the 59% generic prior — no recent match evidence."],
    )
    base.update(over)
    return render_proposal(**base)


class TestTheThreeQuestions:
    def test_it_says_why_this_player(self):
        out = _render()
        assert "82.6" in out and "Bayern" in out

    def test_it_says_why_it_improves_the_lineup(self):
        out = _render()
        assert "Klaas" in out
        assert "25.5" in out
        assert "57.2" in out

    def test_it_says_why_this_price(self):
        out = _render()
        assert "32,285,629" in out
        assert "32,608,485" in out
        assert "1.0%" in out


class TestBudget:
    def test_it_shows_the_budget_after_the_trade(self):
        assert "62,708,629" in _render()


class TestRisks:
    def test_risks_are_shown(self):
        assert "generic prior" in _render()

    def test_no_risk_section_when_there_are_none(self):
        assert "RISKS" not in _render(risks=[])


class TestNoCrashOnEdges:
    def test_zero_market_value_does_not_divide_by_zero(self):
        out = _render(market_value=0)
        assert isinstance(out, str) and out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_proposal_render.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.notify'`

- [ ] **Step 3: Write minimal implementation**

Create `rehoboam/notify/__init__.py` (empty file), then `rehoboam/notify/render.py`:

```python
"""Rendering decisions into text a human can act on.

Pure functions. Every number here is one the bot computed and can show its
working for — the point of the message is that approving takes one tap because
the case is already complete.
"""

from __future__ import annotations


def render_proposal(
    *,
    player_name: str,
    club: str,
    bid: int,
    market_value: int,
    ep: float,
    displaced_name: str,
    displaced_ep: float,
    marginal_gain: float,
    budget_before: int,
    trend_7d_pct: float | None,
    risks: list[str],
) -> str:
    """The three questions Marco asked for, each as its own section."""
    overbid = ((bid / market_value - 1.0) * 100.0) if market_value > 0 else 0.0
    trend = f"{trend_7d_pct:+.1f}%/7d" if trend_7d_pct is not None else "unknown"
    budget_after = budget_before - bid

    lines = [
        f"BUY {player_name} — EUR {bid:,}",
        "",
        "WHY THIS PLAYER",
        f"  EP {ep:.1f}. {club}.",
        "",
        "WHY IT IMPROVES THE LINEUP",
        f"  Displaces {displaced_name} ({displaced_ep:.1f}) in the best eleven.",
        f"  Net gain {marginal_gain:+.1f} points per matchday.",
        "",
        "WHY THIS PRICE",
        f"  Market value EUR {market_value:,}; bid +{overbid:.1f}%. Trend {trend}.",
        f"  Budget EUR {budget_before:,} -> EUR {budget_after:,}.",
    ]
    if risks:
        lines += ["", "RISKS"] + [f"  {r}" for r in risks]
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_proposal_render.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Full suite, lint, commit**

```bash
uv run pytest -q -m "not slow" && uv run ruff check rehoboam/ tests/
git add rehoboam/notify/ tests/test_proposal_render.py
git commit -m "feat(notify): render a trade proposal with its full reasoning"
```

______________________________________________________________________

### Task 4: Telegram notifier

**Files:**

- Create: `rehoboam/notify/telegram.py`
- Modify: `rehoboam/config.py` (three fields)
- Test: `tests/test_telegram_notifier.py`

**Interfaces:**

- Consumes: nothing.

- Produces: `send_proposal(token: str, chat_id: str, proposal_id: str, text: str, *, timeout: float = 10.0) -> bool`

- New `Settings`: `telegram_bot_token: str = ""`, `telegram_chat_id: str = ""`, `telegram_webhook_secret: str = ""`

- [ ] **Step 1: Write the failing test**

Create `tests/test_telegram_notifier.py`:

```python
"""Telegram delivery.

Notification failures must NEVER block trading — the project's existing
best-effort pattern. These tests pin that a dead Telegram is survivable.
"""

from unittest.mock import MagicMock, patch

from rehoboam.notify.telegram import send_proposal


class TestSuccess:
    def test_it_posts_to_the_send_message_endpoint(self):
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            assert send_proposal("T", "C", "p1", "hello") is True
        url = post.call_args[0][0]
        assert "api.telegram.org/botT/sendMessage" in url

    def test_it_attaches_approve_and_reject_buttons_carrying_the_id(self):
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            send_proposal("T", "C", "p1", "hello")
        markup = post.call_args[1]["json"]["reply_markup"]
        data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
        assert "approve:p1" in data
        assert "reject:p1" in data


class TestFailuresAreSurvivable:
    def test_a_non_200_returns_false_and_does_not_raise(self):
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=500)
            assert send_proposal("T", "C", "p1", "hello") is False

    def test_a_network_error_returns_false_and_does_not_raise(self):
        with patch(
            "rehoboam.notify.telegram.requests.post", side_effect=OSError("down")
        ):
            assert send_proposal("T", "C", "p1", "hello") is False

    def test_missing_credentials_return_false_without_calling_out(self):
        with patch("rehoboam.notify.telegram.requests.post") as post:
            assert send_proposal("", "", "p1", "hello") is False
            post.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_telegram_notifier.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.notify.telegram'`

- [ ] **Step 3: Write minimal implementation**

Create `rehoboam/notify/telegram.py`:

```python
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
    except Exception:
        logger.warning(
            "telegram: send failed for proposal %s", proposal_id, exc_info=True
        )
        return False

    if resp.status_code != 200:
        logger.warning(
            "telegram: send returned %s for proposal %s", resp.status_code, proposal_id
        )
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_telegram_notifier.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Add the Settings fields**

In `rehoboam/config.py`:

```python
    telegram_bot_token: str = Field(
        default="",
        repr=False,
        description="Telegram bot token. Empty disables Telegram entirely.",
    )
    telegram_chat_id: str = Field(
        default="",
        description="Telegram chat to send trade proposals to.",
    )
    telegram_webhook_secret: str = Field(
        default="",
        repr=False,
        description=(
            "Shared secret Telegram echoes in X-Telegram-Bot-Api-Secret-Token. The "
            "approval webhook is a public endpoint that spends money, so a callback "
            "without this header is rejected before anything is read from it."
        ),
    )
```

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q -m "not slow" && uv run ruff check rehoboam/ tests/
git add rehoboam/notify/telegram.py rehoboam/config.py tests/test_telegram_notifier.py
git commit -m "feat(notify): telegram proposal delivery, best-effort"
```

______________________________________________________________________

### Task 5: Propose instead of executing improvement buys

The behaviour change. Improvement buys stop executing and become proposals.

**Files:**

- Modify: `rehoboam/auto_trader.py` (`run_unified_trade_phase`, the `kind == "buy"` branch)
- Test: `tests/test_proposal_wiring.py`

**Interfaces:**

- Consumes: `BidLearner.record_proposal` (Task 2), `render_proposal` (Task 3), `send_proposal` (Task 4).

- Produces: `AutoTrader._propose_buy(league, rec, ctx) -> bool` — records, renders, sends; returns True if a proposal was recorded.

- [ ] **Step 1: Write the failing test**

Create `tests/test_proposal_wiring.py`:

```python
"""Improvement buys become proposals, not purchases.

The whole point of the approval gate: `api.buy_player` must not be reached for
a squad-improving buy without Marco tapping approve.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rehoboam.auto_trader import AutoTrader
from rehoboam.config import Settings


@pytest.fixture
def trader(tmp_path, monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)
    return AutoTrader(api=MagicMock(), settings=Settings(), dry_run=False)


def _rec():
    return SimpleNamespace(
        player=SimpleNamespace(
            id="6080",
            first_name="Aleksandar",
            last_name="Pavlović",
            market_value=32_285_629,
            team_name="Bayern",
        ),
        score=SimpleNamespace(expected_points=82.6),
        marginal_ep_gain=57.2,
        recommended_bid=32_608_485,
        replaces_player_name="Klaas",
        replaces_player_ep=25.5,
        reason="upgrade",
    )


def _ctx():
    return SimpleNamespace(current_budget=95_317_114, ep_result={})


class TestProposalReplacesPurchase:
    def test_it_records_a_proposal(self, trader):
        with patch("rehoboam.notify.telegram.send_proposal", return_value=True):
            assert trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx()) is True
        assert len(trader.learner.pending_proposals()) == 1

    def test_it_does_not_buy(self, trader):
        with patch("rehoboam.notify.telegram.send_proposal", return_value=True):
            trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx())
        trader.api.buy_player.assert_not_called()

    def test_the_stored_message_carries_the_reasoning(self, trader):
        with patch("rehoboam.notify.telegram.send_proposal", return_value=True):
            trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx())
        msg = trader.learner.pending_proposals()[0]["message"]
        assert "Klaas" in msg and "57.2" in msg


class TestTelegramFailureIsSurvivable:
    def test_a_failed_send_still_records_the_proposal(self, trader):
        """So it appears in the daily email even if Telegram was down."""
        with patch("rehoboam.notify.telegram.send_proposal", return_value=False):
            assert trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx()) is True
        assert len(trader.learner.pending_proposals()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_proposal_wiring.py -q`
Expected: FAIL with `AttributeError: 'AutoTrader' object has no attribute '_propose_buy'`

- [ ] **Step 3: Write the method**

In `rehoboam/auto_trader.py`, as a method on `AutoTrader`:

```python
def _propose_buy(self, league, rec, ctx) -> bool:
    """Record and send a proposal instead of buying. True if recorded.

    The proposal is recorded FIRST and sent second, so a Telegram outage
    loses the notification but not the decision — it still surfaces in the
    daily email.
    """
    import uuid

    from .notify.render import render_proposal
    from .notify.telegram import send_proposal

    proposal_id = uuid.uuid4().hex[:12]
    player = rec.player
    trend = None
    try:
        from .trader import Trader

        trend = (
            Trader(self.api, self.settings)
            .trend_service.get_trend(player.id, player.market_value, league.id)
            .trend_7d_pct
        )
    except Exception:
        logger.debug("proposal: no trend for %s", player.id, exc_info=True)

    risks: list[str] = []
    if getattr(rec.score, "data_quality", None) and rec.score.data_quality.grade != "A":
        risks.append(
            f"Data quality {rec.score.data_quality.grade} — no fitted history, "
            "scored on the position prior."
        )

    message = render_proposal(
        player_name=f"{player.first_name} {player.last_name}".strip(),
        club=getattr(player, "team_name", "") or "unknown club",
        bid=int(rec.recommended_bid),
        market_value=int(player.market_value),
        ep=float(rec.score.expected_points),
        displaced_name=getattr(rec, "replaces_player_name", None)
        or "the weakest starter",
        displaced_ep=float(getattr(rec, "replaces_player_ep", 0.0) or 0.0),
        marginal_gain=float(rec.marginal_ep_gain),
        budget_before=int(ctx.current_budget),
        trend_7d_pct=trend,
        risks=risks,
    )

    try:
        self.learner.record_proposal(
            proposal_id=proposal_id,
            player_id=player.id,
            player_name=player.last_name,
            bid=int(rec.recommended_bid),
            market_value=int(player.market_value),
            message=message,
        )
    except Exception:
        logger.exception("proposal: could not record %s", proposal_id)
        return False

    send_proposal(
        self.settings.telegram_bot_token,
        self.settings.telegram_chat_id,
        proposal_id,
        message,
    )
    logger.info(
        "proposal recorded id=%s player=%s bid=%d",
        proposal_id,
        player.id,
        int(rec.recommended_bid),
    )
    return True
```

`AutoTrader` has no `trend_service` of its own — it constructs a local `Trader` wherever it needs one (`auto_trader.py:1043`, `:1126`, `:1402`). The code above follows that existing pattern. If the trend lookup fails the renderer accepts `None` and prints "unknown", so this is never fatal.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_proposal_wiring.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Route improvement buys through it**

In `run_unified_trade_phase`, in the `if kind == "buy":` branch, replace the `self.execution.buy(...)` call and its result handling with:

```python
if self._propose_buy(league, obj, ctx):
    console.print(f"[cyan]Proposed {obj.player.last_name} — awaiting approval[/cyan]")
    available_slots -= 1
continue
```

Decrementing `available_slots` reserves the slot while approval is pending, so a single run does not propose more buys than there is room for.

**Leave the `kind == "pair"` branch alone.** Trade pairs sell before they bid, and splitting that across an approval round trip would leave the squad short for hours. Pairs are a separate decision.

- [ ] **Step 6: Full suite, lint, live smoke**

```bash
uv run pytest -q -m "not slow" && uv run ruff check rehoboam/ tests/
uv run rehoboam status 2>&1 | grep -iE "proposed|awaiting approval"
```

Expected: tests pass; the dry run reports a proposal rather than a purchase.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/auto_trader.py tests/test_proposal_wiring.py
git commit -m "feat(trade): improvement buys become proposals awaiting approval"
```

______________________________________________________________________

### Task 6: Approval webhook

A public endpoint that spends money. Treated accordingly.

**Files:**

- Create: `rehoboam/notify/approval.py`
- Modify: `deploy/azure_function/function_app.py`
- Test: `tests/test_approval_webhook.py`

**Interfaces:**

- Consumes: `check_buy` (Task 1), `get_proposal`/`mark_proposal` (Task 2).

- Produces: `handle_callback(body: dict, secret_header: str | None, *, settings, learner, api, league) -> str` — returns the reply text to show in Telegram.

- [ ] **Step 1: Write the failing test**

Create `tests/test_approval_webhook.py`:

```python
"""The approval webhook is a public endpoint that spends money.

These are adversarial tests, not happy-path ones: forged callbacks, replayed
callbacks, and stale proposals are the failure modes that cost real money.
"""

from unittest.mock import MagicMock

import pytest

from rehoboam.bid_learner import BidLearner
from rehoboam.config import Settings
from rehoboam.notify.approval import handle_callback


@pytest.fixture
def learner(tmp_path):
    lr = BidLearner(db_path=tmp_path / "bids.db")
    lr.record_proposal(
        proposal_id="p1",
        player_id="6080",
        player_name="Pavlović",
        bid=32_608_485,
        market_value=32_285_629,
        message="BUY",
    )
    return lr


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "t@e.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "t")
    s = Settings()
    s.telegram_webhook_secret = "s3cret"
    s.max_overbid_pct = 8.0
    return s


@pytest.fixture
def api():
    a = MagicMock()
    a.get_market.return_value = [
        MagicMock(id="6080", market_value=32_285_629, last_name="Pavlović")
    ]
    a.get_squad.return_value = [MagicMock(id=f"s{i}") for i in range(11)]
    a.get_my_bids.return_value = []
    a.get_team_info.return_value = {"budget": 95_317_114}
    return a


def _cb(action="approve", pid="p1"):
    return {"callback_query": {"id": "cb1", "data": f"{action}:{pid}"}}


class TestAuthentication:
    def test_a_missing_secret_is_rejected(self, learner, settings, api):
        out = handle_callback(
            _cb(), None, settings=settings, learner=learner, api=api, league=MagicMock()
        )
        assert "unauthor" in out.lower()
        api.buy_player.assert_not_called()

    def test_a_wrong_secret_is_rejected(self, learner, settings, api):
        out = handle_callback(
            _cb(),
            "wrong",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        assert "unauthor" in out.lower()
        api.buy_player.assert_not_called()

    def test_the_proposal_is_untouched_after_a_forged_callback(
        self, learner, settings, api
    ):
        handle_callback(
            _cb(),
            "wrong",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        assert learner.get_proposal("p1")["status"] == "pending"


class TestApproval:
    def test_a_valid_approval_buys(self, learner, settings, api):
        handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        api.buy_player.assert_called_once()

    def test_a_valid_approval_marks_the_proposal(self, learner, settings, api):
        handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        assert learner.get_proposal("p1")["status"] == "executed"


class TestReplay:
    def test_a_second_identical_callback_does_not_buy_twice(
        self, learner, settings, api
    ):
        """Telegram retries callbacks. Buying twice is real money."""
        handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        assert api.buy_player.call_count == 1


class TestRejection:
    def test_reject_marks_and_does_not_buy(self, learner, settings, api):
        handle_callback(
            _cb("reject"),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        api.buy_player.assert_not_called()
        assert learner.get_proposal("p1")["status"] == "rejected"


class TestRevalidation:
    def test_a_player_no_longer_on_the_market_is_not_bought(
        self, learner, settings, api
    ):
        api.get_market.return_value = []
        out = handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        api.buy_player.assert_not_called()
        assert "no longer" in out.lower() or "unknown player" in out.lower()

    def test_a_price_that_moved_past_the_cap_is_not_bought(
        self, learner, settings, api
    ):
        """Market values update daily after 10:00 — a proposal's price is stale."""
        api.get_market.return_value = [
            MagicMock(id="6080", market_value=20_000_000, last_name="Pavlović")
        ]
        handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        api.buy_player.assert_not_called()

    def test_an_unknown_proposal_id_is_reported_not_executed(
        self, learner, settings, api
    ):
        out = handle_callback(
            _cb(pid="nope"),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        api.buy_player.assert_not_called()
        assert "not found" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_approval_webhook.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.notify.approval'`

- [ ] **Step 3: Write minimal implementation**

Create `rehoboam/notify/approval.py`:

```python
"""Handling a Telegram approval callback.

This runs behind a public HTTP endpoint and can spend money, so the order of
operations matters and is deliberate:

    authenticate -> claim the proposal -> re-validate -> gate -> execute

Claiming before validating is what makes a replayed callback safe: the second
call finds the proposal already out of 'pending' and stops. Re-validating after
claiming is what makes a stale proposal safe: market values update daily after
10:00, so the numbers in the message are out of date by construction and must
never be the numbers we act on.
"""

from __future__ import annotations

import logging

from rehoboam.services.safety_gate import check_buy

logger = logging.getLogger(__name__)


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
    expected = settings.telegram_webhook_secret
    if not expected or secret_header != expected:
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

    market = {p.id: p for p in api.get_market(league)}
    live = market.get(proposal["player_id"])
    if live is None:
        learner.set_proposal_status(proposal_id, "failed")
        return f"{proposal['player_name']} is no longer on the market."

    squad = api.get_squad(league)
    bids = api.get_my_bids(league)
    budget = int(api.get_team_info(league).get("budget", 0))
    free_slots = 15 - len(squad) - len(bids)

    result = check_buy(
        player_id=proposal["player_id"],
        bid=int(proposal["bid"]),
        market_value=int(live.market_value),
        current_budget=budget,
        free_slots=free_slots,
        known_player_ids=market.keys(),
        max_overbid_pct=settings.max_overbid_pct,
    )
    if not result.ok:
        learner.set_proposal_status(proposal_id, "failed")
        return "Not executed:\n" + "\n".join(f"- {r}" for r in result.reasons)

    try:
        api.buy_player(league, live, int(proposal["bid"]))
    except Exception as exc:
        learner.set_proposal_status(proposal_id, "failed")
        logger.exception("approval: buy failed for %s", proposal_id)
        return f"Buy failed: {exc}"

    learner.set_proposal_status(proposal_id, "executed")
    return f"Bought {proposal['player_name']} for EUR {int(proposal['bid']):,}."
```

The two methods are deliberately different. `mark_proposal` moves a row out of
`pending` and returns False if it already left — that single UPDATE is the
entire replay guard, so a retried Telegram callback stops there. Once claimed,
the callback owns the proposal and reports its outcome with
`set_proposal_status`, which has no guard because the claim already happened.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_approval_webhook.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: Add the HTTP trigger**

In `deploy/azure_function/function_app.py`, alongside the existing timer trigger:

```python
@app.route(route="telegram", auth_level=func.AuthLevel.FUNCTION)
def telegram_approval(req: func.HttpRequest) -> func.HttpResponse:
    """Telegram approval callbacks. Public endpoint — see notify/approval.py."""
    import json

    from rehoboam.api import KickbaseAPI
    from rehoboam.bid_learner import BidLearner
    from rehoboam.config import get_settings
    from rehoboam.notify.approval import handle_callback

    os.chdir(TEMP_DIR)
    os.makedirs(f"{TEMP_DIR}/logs", exist_ok=True)
    download_databases()

    settings = get_settings()
    api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
    api.login()
    league = api.get_leagues()[settings.league_index]

    reply = handle_callback(
        req.get_json(),
        req.headers.get("X-Telegram-Bot-Api-Secret-Token"),
        settings=settings,
        learner=BidLearner(),
        api=api,
        league=league,
    )
    upload_databases()
    logging.info("telegram approval: %s", reply)
    return func.HttpResponse(json.dumps({"text": reply}), mimetype="application/json")
```

`download_databases()` and `upload_databases()` are the existing module-level
helpers in this same file (defined at `:27` and `:46`), already used by the
timer trigger. Both calls are mandatory and ordered: proposals live in
`bid_learning.db`, which is blob-synced, so reading before the download finds an
empty table and writing without the upload loses the approval entirely.

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q -m "not slow" && uv run ruff check rehoboam/ tests/
git add rehoboam/notify/approval.py deploy/azure_function/function_app.py tests/test_approval_webhook.py
git commit -m "feat(notify): telegram approval webhook with replay and staleness guards"
```

______________________________________________________________________

### Task 7: Daily email summary

**Files:**

- Create: `rehoboam/notify/email.py`
- Modify: `rehoboam/notify/render.py` (add `render_daily_summary`), `rehoboam/config.py` (five fields)
- Test: `tests/test_daily_email.py`

**Interfaces:**

- Consumes: `pending_proposals` (Task 2).

- Produces: `render_daily_summary(*, lineup, squad, budget, market, pending, executed, rejections) -> str`
  and `send_email(*, host, port, user, password, sender, recipient, subject, body) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_daily_email.py`:

```python
"""The daily summary email."""

from unittest.mock import MagicMock, patch

from rehoboam.notify.email import send_email
from rehoboam.notify.render import render_daily_summary


def _summary(**over):
    base = dict(
        lineup=[
            ("Flekken", 52.0, None),
            ("Pavlović", 82.6, None),
            ("Stark", 26.7, "uncertain"),
        ],
        squad_size=11,
        budget=95_317_114,
        market=[("Gyamerah", 7_583_425, 25.6), ("Lynen", 10_417_894, 19.7)],
        pending=[("Pavlović", 32_608_485)],
        executed=["Sold Höler for EUR 3,249,970"],
        rejections=["overbid 18.7% exceeds the 8.0% cap"],
    )
    base.update(over)
    return render_daily_summary(**base)


class TestContent:
    def test_it_lists_the_lineup_with_ep(self):
        out = _summary()
        assert "Pavlović" in out and "82.6" in out

    def test_it_flags_players_with_a_status_note(self):
        assert "uncertain" in _summary()

    def test_it_shows_the_budget(self):
        assert "95,317,114" in _summary()

    def test_it_lists_pending_proposals(self):
        assert "32,608,485" in _summary()

    def test_it_reports_gate_rejections(self):
        """A limit that keeps firing is a signal, not something to hide."""
        assert "8.0% cap" in _summary()

    def test_it_reports_proposal_volume_for_approval_fatigue(self):
        assert "1" in _summary()


class TestDelivery:
    def test_it_sends_over_smtp(self):
        with patch("rehoboam.notify.email.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value = MagicMock()
            assert (
                send_email(
                    host="h",
                    port=587,
                    user="u",
                    password="p",
                    sender="a@b.c",
                    recipient="d@e.f",
                    subject="s",
                    body="b",
                )
                is True
            )

    def test_a_failure_returns_false_and_does_not_raise(self):
        with patch("rehoboam.notify.email.smtplib.SMTP", side_effect=OSError("down")):
            assert (
                send_email(
                    host="h",
                    port=587,
                    user="u",
                    password="p",
                    sender="a@b.c",
                    recipient="d@e.f",
                    subject="s",
                    body="b",
                )
                is False
            )

    def test_missing_config_returns_false_without_connecting(self):
        with patch("rehoboam.notify.email.smtplib.SMTP") as smtp:
            assert (
                send_email(
                    host="",
                    port=587,
                    user="",
                    password="",
                    sender="",
                    recipient="",
                    subject="s",
                    body="b",
                )
                is False
            )
            smtp.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daily_email.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.notify.email'`

- [ ] **Step 3: Add the renderer**

Append to `rehoboam/notify/render.py`:

```python
def render_daily_summary(
    *,
    lineup: list[tuple[str, float, str | None]],
    squad_size: int,
    budget: int,
    market: list[tuple[str, int, float]],
    pending: list[tuple[str, int]],
    executed: list[str],
    rejections: list[str],
) -> str:
    """The once-a-day picture: lineup, market, what happened, what was blocked.

    Proposal volume is reported explicitly. If approvals start arriving daily
    rather than weekly, that is the signal the approval gate has become the
    daily loop it was meant to replace.
    """
    lines = [
        f"SQUAD {squad_size}/15   BUDGET EUR {budget:,}",
        "",
        "LINEUP",
    ]
    for name, ep, flag in lineup:
        note = f"  [{flag}]" if flag else ""
        lines.append(f"  {name:<16} {ep:>6.1f}{note}")

    lines += ["", "MARKET"]
    for name, mv, trend in market:
        lines.append(f"  {name:<16} EUR {mv:>12,}  {trend:+.1f}%/7d")

    lines += ["", f"PENDING PROPOSALS ({len(pending)})"]
    for name, bid in pending:
        lines.append(f"  {name:<16} EUR {bid:>12,}  awaiting approval")

    lines += ["", "EXECUTED (24h)"] + ([f"  {e}" for e in executed] or ["  nothing"])

    if rejections:
        lines += ["", "BLOCKED BY SAFETY GATE"] + [f"  {r}" for r in rejections]

    return "\n".join(lines)
```

- [ ] **Step 4: Write the sender**

Create `rehoboam/notify/email.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_daily_email.py -q`
Expected: PASS (9 passed)

- [ ] **Step 6: Add the Settings fields**

In `rehoboam/config.py`:

```python
    smtp_host: str = Field(default="", description="SMTP host. Empty disables email.")
    smtp_port: int = Field(default=587, description="SMTP port; 587 for STARTTLS.")
    smtp_user: str = Field(default="", repr=False, description="SMTP username.")
    smtp_password: str = Field(default="", repr=False, description="SMTP password or app password.")
    alert_email_to: str = Field(default="", description="Recipient of the daily summary.")
```

The sender address reuses `smtp_user`. Populate all of these as Key Vault
references in the Function app, exactly as `KICKBASE_PASSWORD` is.

- [ ] **Step 7: Send it once per day from the timer**

In `deploy/azure_function/function_app.py`'s `trading_session`, after the
session completes, build the summary from the session result and send it —
guarded so only the morning run emails:

```python
if datetime.now(tz=timezone.utc).hour < 12:
    try:
        _send_daily_summary(api, league, settings, session)
    except Exception:
        logging.warning("daily summary failed", exc_info=True)
```

Add `_send_daily_summary` as a module-level helper in the same file, gathering
lineup, squad, budget, market, `learner.pending_proposals()`, the session's
executed trades and any gate rejections, then calling `render_daily_summary`
and `send_email`.

- [ ] **Step 8: Full suite, lint, commit**

```bash
uv run pytest -q -m "not slow" && uv run ruff check rehoboam/ tests/
git add rehoboam/notify/ rehoboam/config.py deploy/azure_function/function_app.py tests/test_daily_email.py
git commit -m "feat(notify): daily summary email over SMTP"
```

______________________________________________________________________

## Deployment, after all tasks

Not code, but the plan is not done until these exist. None belong in the repo:

1. Create the Telegram bot via `@BotFather`; note the token and your chat id.
1. Store `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` as Key Vault references surfaced as app settings on `func-rehoboam`, matching `KICKBASE_PASSWORD`.
1. Register the webhook, including the function key and the secret:

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://func-rehoboam.azurewebsites.net/api/telegram?code=<FUNCTION_KEY>" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

4. Verify with `getWebhookInfo` that `last_error_message` is empty.

## Deliberately deferred

**Trade pairs still execute autonomously.** Only plain improvement buys become proposals. Pairs sell before they bid, and splitting that across an approval round trip leaves the squad short for hours — REH-87 already bounds that risk with a recovery-time gate. Routing pairs through approval is a separate decision.

**Proposal expiry is not implemented.** The spec calls for it. Re-validation covers the dangerous half — a stale proposal cannot execute at a bad price — so expiry is a tidiness feature and can follow once real approval latency is observed.

**No LLM anywhere.** Dropped on cost. The natural place to add one later is inside `_propose_buy`, enriching `risks` with injury news and transfer rumours before the message is rendered — one call per proposal rather than per run, so a few euros a month rather than $25.
