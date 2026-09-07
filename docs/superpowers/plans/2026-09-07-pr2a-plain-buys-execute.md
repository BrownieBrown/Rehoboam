# PR 2a — Plain Buys Execute, the Session Board, `offers=` Counters: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A plain squad-improvement buy is executed by the session through the safety gate — no proposal, no tap — and the session reports what it did: a one-message board with every offer placed or refused, rows in `trade_proposals` written after the fact, and `offers=N refused=M` beside `trades=` in the session-end log.

**Architecture:** `_propose_buy` becomes `_execute_buy`: same trend floor, same rendered case, then `ExecutionService.buy(..., gate=_build_buy_gate(...))` — the path trade pairs and (since PR 1) the emergency fill already use. The row in `trade_proposals` is written *after* the result is known (`executed` / `refused` / `failed`, gate reasons in `message`); `record_proposal` gains a `status` parameter for that. `notify/overview.py` loses the budget-split proposal board and gains `render_session_board` over `OfferLine`s that carry an `outcome`; `_send_proposal_overview` becomes `_send_session_board`, sent with `send_message` and no keyboard. The `proposed_slots` bookkeeping, `_has_pending_proposal`, `_needs_sell_plan`, `proposals_for_player`, `send_overview` and `overview_keyboard` go. The Telegram webhook (`notify/approval.py`) is untouched: it still serves any `pending` rows that predate this PR, and spec §4 reuses it. The outcome ledger and the daily-summary rewrite are PR 2b.

**Tech Stack:** Python 3.12 via `uv`; pytest; SQLite (`BidLearner`); Rich console; Telegram Bot API via `notify/telegram.py`; pre-commit (ruff, black, bandit, mdformat).

**Spec:** `docs/superpowers/specs/2026-09-05-autonomous-wallet-design.md` — §1 (all of it), Rollout item 2 (first half). §6 is PR 2b.

## Global Constraints

- Work on branch `marcobraun2013/pr2a-plain-buys-execute`, created from `marcobraun2013/pr1-bids-are-commitments` at `d54fdcd` (PR #103, open). Never commit to `main`. If #103 has merged by the time this PR opens, rebase onto `main` and target `main`; otherwise target PR 1's branch (a stacked PR).
- Run everything through `uv`: `uv run pytest`, `uv run ruff check`, `uv run pre-commit run --files …`. Do not invoke `black` by hand on a file — the repo is not black-clean and whole-file formatting produces unrelated churn; the pre-commit hook handles staged changes at commit time (if it reformats a staged file, re-stage and commit again).
- Every commit message ends with these two trailer lines:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01UXD6K51WgvWan8PuqYbnxv
  ```
- Every buy goes through `ExecutionService.buy` with a `BuyGate` built by `_build_buy_gate` (REH-100). No call site may reach `api.buy_player` any other way.
- `spendable_budget` for a plain buy is `ctx.flip_budget` — the allowance the matchday phase permits (aggressive: `current_budget + max_debt`), never the raw wallet. `BuyGate`'s docstring in `services/safety_gate.py` explains why; the kickoff-lockout guard inside `ExecutionService.buy` is what keeps the raw balance non-negative where it matters.
- `rehoboam status` (dry-run) must stay read-only: `ExecutionService.buy` already returns a success result without touching the API in dry-run, and `_execute_buy` must write no `trade_proposals` row and send no Telegram message in dry-run. It still appends to the board so `status` shows what a live run would send.
- Statuses written by the session: `executed` (offer placed), `refused` (safety gate said no; reasons appended to `message` under a `REFUSED` heading), `failed` (Kickbase or the network said no; the error appended under `FAILED`). `pending` remains the default of `record_proposal` for the webhook's existing rows. `offers_refused` counts both `refused` and `failed` — "not placed".
- Tests set `KICKBASE_EMAIL=test@example.com` / `KICKBASE_PASSWORD=test` via `monkeypatch.setenv` and `monkeypatch.chdir(tmp_path)` so `BidLearner` writes under `tmp_path`.

______________________________________________________________________

## File Structure

| file                                                                                                                                                             | responsibility after this PR                                                                                                                                                                                                                                                                                                                                                                                |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `rehoboam/notify/overview.py`                                                                                                                                    | `OfferLine` (was `ProposalLine`; gains `outcome`, `detail`; loses `is_emergency`) and `render_session_board`; `split_by_budget` and `render_proposal_overview` deleted                                                                                                                                                                                                                                      |
| `rehoboam/notify/telegram.py`                                                                                                                                    | `send_overview` and `overview_keyboard` deleted; `send_message`, `approval_keyboard`, `send_proposal` untouched                                                                                                                                                                                                                                                                                             |
| `rehoboam/bid_learner.py`                                                                                                                                        | `record_proposal(..., status="pending")`; `proposals_for_player` deleted                                                                                                                                                                                                                                                                                                                                    |
| `rehoboam/auto_trader.py`                                                                                                                                        | `_execute_buy` (was `_propose_buy`), `_offer_line` (was `_proposal_line`), `_send_session_board` (was `_send_proposal_overview`), `_is_too_falling_to_buy` (was `…_to_propose`); the unified phase's buy branch executes; `proposed_slots`, `_has_pending_proposal`, `_needs_sell_plan` deleted; `EPSessionContext` and `AutoTradeSession` gain `offers_placed` / `offers_refused`; `session-end` logs them |
| `deploy/azure_function/function_app.py`                                                                                                                          | `_send_daily_summary` labels executed rows `OFFERED` (not `APPROVED`) and lists `refused` among the blocked — the minimum so the summary stops lying until PR 2b rewrites it                                                                                                                                                                                                                                |
| `CLAUDE.md`                                                                                                                                                      | the "Approval gate (2026-08-24)" bullet becomes the autonomous-wallet bullet; the `status` bullet names `_execute_buy`                                                                                                                                                                                                                                                                                      |
| `tests/test_session_board.py`                                                                                                                                    | **new** — replaces `tests/test_proposal_overview.py` (deleted)                                                                                                                                                                                                                                                                                                                                              |
| `tests/test_plain_buys_execute.py`                                                                                                                               | **new** — replaces `tests/test_proposal_wiring.py` (deleted); real `ExecutionService` + real `LearningTracker` against a mock API                                                                                                                                                                                                                                                                           |
| `tests/test_proposal_store.py`                                                                                                                                   | two tests added for the `status` parameter                                                                                                                                                                                                                                                                                                                                                                  |
| `tests/test_auto_trader_flip_switches.py`, `tests/test_falling_mv_block.py`, `tests/test_emergency_fill_executes.py`, `tests/test_emergency_fill_every_phase.py` | renamed symbols; one new session-level test for the counters                                                                                                                                                                                                                                                                                                                                                |

______________________________________________________________________

### Task 0: Branch and baseline

**Files:** none

- [ ] **Step 1: Confirm the branch and its base**

```bash
git branch --show-current
git log --oneline -1
git merge-base marcobraun2013/pr1-bids-are-commitments HEAD | cut -c1-7
```

Expected: `marcobraun2013/pr2a-plain-buys-execute`; the last two lines both show `d54fdcd`. (The plan commit itself sits on top; that is fine.)

- [ ] **Step 2: Confirm the suite is green before touching anything**

Run: `uv run pytest -q -x 2>&1 | tail -3`
Expected: `1585 passed, 1 skipped` (or more passed if #103 gained commits). Any failure here is not this PR's problem — stop and report it.

______________________________________________________________________

### Task 1: `record_proposal(status=…)`; drop `proposals_for_player`

**Files:**

- Modify: `rehoboam/bid_learner.py` (`record_proposal`, ~line 1903; delete `proposals_for_player`, ~line 2048)
- Modify: `tests/test_proposal_store.py` (two tests added)

**Interfaces:**

- Consumes: nothing from other tasks.

- Produces: `BidLearner.record_proposal(*, proposal_id, player_id, player_name, bid, market_value, message, tier=None, auto_approve_at=None, batch_id=None, status="pending") -> None`. Task 2 consumes `status`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_proposal_store.py` (it already has a `learner` fixture and a `_record(learner, pid)` helper that calls `record_proposal` with the required fields):

```python
class TestTheSessionWritesTheOutcomeItself:
    """Spec §1: rows are written AFTER the buy, with what happened."""

    def test_a_row_can_be_recorded_as_executed(self, learner):
        learner.record_proposal(
            proposal_id="done",
            player_id="6080",
            player_name="Pavlović",
            bid=32_608_485,
            market_value=32_285_629,
            message="BUY Pavlović — EUR 32,608,485",
            tier="strong_upgrade",
            status="executed",
        )

        assert learner.get_proposal("done")["status"] == "executed"
        assert learner.pending_proposals() == []

    def test_a_refused_row_keeps_the_reason_in_its_message(self, learner):
        learner.record_proposal(
            proposal_id="no",
            player_id="13448",
            player_name="Aouchiche",
            bid=18_835_959,
            market_value=13_451_351,
            message="BUY Aouchiche — EUR 18,835,959\n\nREFUSED\n  overbid 40.0% exceeds the 35.0% ceiling",
            status="refused",
        )

        row = learner.get_proposal("no")
        assert row["status"] == "refused"
        assert "REFUSED" in row["message"]

    def test_the_default_is_still_pending_for_the_webhook(self, learner):
        _record(learner, "p1")

        assert learner.get_proposal("p1")["status"] == "pending"
        assert [p["proposal_id"] for p in learner.pending_proposals()] == ["p1"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_proposal_store.py -q 2>&1 | tail -3`
Expected: the first two FAIL with `TypeError: … got an unexpected keyword argument 'status'`; the third passes.

- [ ] **Step 3: Add the parameter**

In `rehoboam/bid_learner.py`, `record_proposal`: add `status: str = "pending",` as the last keyword parameter (after `batch_id`). Replace the docstring with:

```python
"""Persist an attempted buy and its rendered case.

``status`` is 'pending' for rows the Telegram webhook still serves
(approve / reject). The session itself writes 'executed', 'refused'
or 'failed' AFTER the buy has been attempted (spec §1) — the row is
the record of what happened, not a request for a decision.

``tier`` is the marginal-EP band the bid was sized under; the webhook
recomputes the ceiling from it against the live market value (REH-99).
"""
```

Change the INSERT so the status is a bound parameter: replace

```python
"VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
```

with

```python
"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
```

and in the values tuple insert `status,` immediately after `message,` (so the order is `proposal_id, player_id, player_name, int(bid), int(market_value), message, status, datetime.now().timestamp(), tier, auto_approve_at, batch_id`).

Delete the method `proposals_for_player` (from `def proposals_for_player(self, player_id: str) -> list[dict]:` through its `return [dict(r) for r in rows]`). Its only caller, `_has_pending_proposal`, is deleted in Task 3.

- [ ] **Step 4: Run the store and webhook tests**

Run: `uv run pytest tests/test_proposal_store.py tests/test_telegram_notifier.py tests/test_approval_webhook.py tests/test_approve_all_batch.py -q 2>&1 | tail -3`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/bid_learner.py tests/test_proposal_store.py
git commit -F - <<'MSG'
feat(store): record_proposal takes the outcome

The session writes `trade_proposals` rows after the buy, as executed /
refused / failed (spec §1); 'pending' stays the default for the webhook's
existing rows. `proposals_for_player` served only the re-proposal guard,
which the next commit deletes.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UXD6K51WgvWan8PuqYbnxv
MSG
```

______________________________________________________________________

### Task 2: The session board, `_execute_buy`, the buy branch, the counters

**Files:**

- Modify: `rehoboam/notify/overview.py` (whole file replaced)

- Create: `tests/test_session_board.py`

- Delete: `tests/test_proposal_overview.py`

- Modify: `rehoboam/notify/telegram.py` (delete `overview_keyboard` ~78–107 and `send_overview` ~186–214)

- Modify: `rehoboam/auto_trader.py` — `AutoTradeSession` (~23), `EPSessionContext` (~338), `_is_too_falling_to_propose` (~154), `_proposal_line` (~184), `__init__` (~389), `_propose_buy` (~620), `_send_proposal_overview` (~736), `_has_pending_proposal` + `_needs_sell_plan` (~853–903), `run_unified_trade_phase` buy branch (~1111–1147) and pair guard (~1149–1160) and `proposed_slots` (~1009), `run_full_session` (~1923 and the session-end block ~2216–2284)

- Create: `tests/test_plain_buys_execute.py`

- Delete: `tests/test_proposal_wiring.py`

- Modify: `tests/test_auto_trader_flip_switches.py` (~242–275), `tests/test_falling_mv_block.py`, `tests/test_emergency_fill_executes.py` (~119 and ~227), `tests/test_emergency_fill_every_phase.py` (append one class)

**Interfaces:**

- Consumes: `record_proposal(status=)` (Task 1); `ExecutionService.buy(league, player, price, reason, sell_plan_player_ids=None, *, current_budget, days_until_match, gate) -> AutoTradeResult` (`rehoboam/services/execution.py:75`); `_build_buy_gate(*, settings, ctx, player, spendable_budget, free_slots, marginal_ep_gain, released_player_id=None)` (`auto_trader.py:61`); `send_message(token, chat_id, text, *, approvals=None, timeout=10.0)`.

- Produces: `OfferLine(offer_id: str, name: str, bid: int, ep: float, marginal_gain: float, outcome: str = "placed", detail: str = "", position: str = "", club: str = "", market_value: int = 0, fills_gap: bool = False, trend_7d_pct: float | None = None, season_avg: float | None = None, lineup_probability: int | None = None, minutes_trend: str | None = None, next_opponent: str | None = None, is_dgw: bool = False, squad_at_position: int | None = None, position_minimum: int | None = None, risks: tuple[str, ...] = ())` with properties `overbid_pct`, `is_falling`, `pos_short`; `render_session_board(*, squad_size: int, squad_cap: int, budget_before: int, budget_after: int, placed: list[OfferLine], refused: list[OfferLine]) -> str`; `AutoTrader._execute_buy(self, league, rec, ctx, *, free_slots: int) -> AutoTradeResult | None`; `AutoTrader._send_session_board(self, league, ctx) -> None`; `_is_too_falling_to_buy(trend_7d_pct, settings) -> bool`; `_offer_line(offer_id, rec, bid, trend, risks, *, outcome, detail) -> OfferLine`; `EPSessionContext.offers_placed / offers_refused: int = 0`; `AutoTradeSession.offers_placed / offers_refused: int = 0`; `session-end … offers=%d refused=%d`.

- [ ] **Step 1: Write the failing board tests**

Create `tests/test_session_board.py`:

```python
"""One message for what a session did with the wallet (spec §1).

The proposal board asked Marco to choose; this one tells him what happened.
Every offer the session placed, at what price and why; every candidate the
gate refused and for what reason; the budget before and after. No buttons —
there is nothing left to decide.
"""

from __future__ import annotations

import pytest

from rehoboam.notify.overview import OfferLine, render_session_board

BUDGET_BEFORE = 45_201_758


def _line(name, bid, ep, **kw):
    kw.setdefault("position", "Midfielder")
    kw.setdefault("club", "Bayern")
    kw.setdefault("market_value", int(bid / 1.2))
    return OfferLine(
        offer_id=name.lower(), name=name, bid=bid, ep=ep, marginal_gain=ep, **kw
    )


PAVLOVIC = _line(
    "Pavlović", 32_608_485, 82.6, market_value=32_285_629, trend_7d_pct=1.9
)
DEMAN = _line(
    "Deman", 9_312_505, 50.8, position="Defender", club="Freiburg", fills_gap=True
)
AOUCHICHE = _line(
    "Aouchiche",
    18_835_959,
    68.0,
    position="Forward",
    club="Bochum",
    outcome="refused",
    detail="overbid 40.0% exceeds the 35.0% ceiling for tier must_have",
)
NGUEFACK = _line(
    "Nguefack",
    1_269_172,
    40.0,
    outcome="failed",
    detail="Failed to make offer: 500 - UnderpayNotAllowed",
)

PLACED = [PAVLOVIC, DEMAN]
REFUSED = [AOUCHICHE, NGUEFACK]


def _render(placed=PLACED, refused=REFUSED):
    spend = sum(line.bid for line in placed)
    return render_session_board(
        squad_size=11,
        squad_cap=15,
        budget_before=BUDGET_BEFORE,
        budget_after=BUDGET_BEFORE - spend,
        placed=placed,
        refused=refused,
    )


class TestTheBoardReportsWhatHappened:
    def test_it_leads_with_the_budget_before_and_after(self):
        text = _render()

        assert text.splitlines()[0] == (
            f"SQUAD 11/15   BUDGET EUR {BUDGET_BEFORE:,} -> EUR {BUDGET_BEFORE - 41_920_990:,}"
        )

    def test_it_counts_and_names_every_offer_placed(self):
        text = _render()

        assert "OFFERS PLACED — 2" in text
        assert "Pavlović (MID, Bayern)  EUR 32,608,485" in text
        assert "Deman (DEF, Freiburg)  EUR 9,312,505" in text
        assert "total EUR 41,920,990" in text

    def test_it_counts_and_explains_every_refusal(self):
        text = _render()

        assert "REFUSED — 2" in text
        assert "Aouchiche (FWD, Bochum)  EUR 18,835,959" in text
        assert "! overbid 40.0% exceeds the 35.0% ceiling for tier must_have" in text
        assert "! Failed to make offer: 500 - UnderpayNotAllowed" in text

    def test_placed_come_before_refused(self):
        text = _render()

        assert text.index("OFFERS PLACED") < text.index("REFUSED")

    def test_it_asks_for_nothing(self):
        text = _render()

        assert "Approve" not in text
        assert "RECOMMENDED" not in text
        assert "awaiting" not in text.lower()

    def test_a_session_that_placed_nothing_says_so(self):
        text = _render(placed=[], refused=REFUSED)

        assert "OFFERS PLACED — none" in text
        assert "total EUR" not in text

    def test_a_session_with_no_refusals_has_no_refused_section(self):
        text = _render(placed=PLACED, refused=[])

        assert "REFUSED" not in text


class TestTheLineStillCarriesTheCase:
    def test_the_price_line_shows_the_overbid_and_the_trend(self):
        text = _render()

        assert "MV 32,285,629 · bid +1.0% · trend +1.9%/7d" in text

    def test_a_falling_market_value_is_still_flagged(self):
        falling = _line("Itten", 5_000_000, 40.0, trend_7d_pct=-27.0)

        assert "<-- FALLING" in _render(placed=[falling], refused=[])

    def test_a_gap_filler_is_still_labelled(self):
        assert "fills your DEF gap" in _render(placed=[DEMAN], refused=[])


class TestOfferLine:
    def test_the_default_outcome_is_placed(self):
        assert _line("X", 1_000_000, 10.0).outcome == "placed"

    @pytest.mark.parametrize(
        "bid,mv,expected", [(1_300_000, 1_000_000, 30.0), (900_000, 0, 0.0)]
    )
    def test_overbid_pct(self, bid, mv, expected):
        assert _line("X", bid, 10.0, market_value=mv).overbid_pct == pytest.approx(
            expected
        )

    def test_there_is_no_emergency_flag_any_more(self):
        """The emergency fill executes and never reaches the board (PR 1)."""
        import dataclasses

        assert "is_emergency" not in {f.name for f in dataclasses.fields(OfferLine)}
```

- [ ] **Step 2: Run the board tests to verify they fail**

Run: `uv run pytest tests/test_session_board.py -q 2>&1 | tail -3`
Expected: FAIL — `ImportError: cannot import name 'OfferLine' from 'rehoboam.notify.overview'`.

- [ ] **Step 3: Replace `rehoboam/notify/overview.py`**

Replace the whole file with:

```python
"""One message for what a session did with the wallet (spec §1).

Proposals used to go out and wait for a tap: 13 approvals, 2 acquisitions,
the rest poached while the message sat there (2026-08-29 to 2026-09-02). The
session now places its offers itself, behind the safety gate, and this is the
record: which offers went out, at what price and why; which candidates the
gate refused and for what reason; the budget before and after. No buttons —
there is nothing left to decide.

Pure, so the message can be asserted directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Below this weekly market-value move a line is called out as falling.
#: Itten went out at -27.0%/7d with the number printed and nothing flagged.
FALLING_TREND_PCT = -10.0

_POSITION_ABBR = {
    "Goalkeeper": "GK",
    "Defender": "DEF",
    "Midfielder": "MID",
    "Forward": "FWD",
}


@dataclass(frozen=True)
class OfferLine:
    """One attempted buy, with everything the message needs to justify it.

    The pipeline already computes all of this — `PlayerScore` carries
    position, lineup probability, minutes trend, average points and next
    opponent — and the first renderer discarded it and printed "unknown club".
    """

    offer_id: str
    name: str
    bid: int
    ep: float
    marginal_gain: float
    #: "placed" — the offer is live. "refused" — the safety gate said no.
    #: "failed" — Kickbase or the network said no. `detail` carries the why.
    outcome: str = "placed"
    detail: str = ""
    position: str = ""
    club: str = ""
    market_value: int = 0
    fills_gap: bool = False
    trend_7d_pct: float | None = None
    season_avg: float | None = None
    lineup_probability: int | None = None
    minutes_trend: str | None = None
    next_opponent: str | None = None
    is_dgw: bool = False
    squad_at_position: int | None = None
    position_minimum: int | None = None
    risks: tuple[str, ...] = field(default_factory=tuple)

    @property
    def overbid_pct(self) -> float:
        if self.market_value <= 0:
            return 0.0
        return (self.bid / self.market_value - 1.0) * 100.0

    @property
    def is_falling(self) -> bool:
        return self.trend_7d_pct is not None and self.trend_7d_pct <= FALLING_TREND_PCT

    @property
    def pos_short(self) -> str:
        return _POSITION_ABBR.get(self.position, self.position[:3].upper() or "???")


def _availability(line: OfferLine) -> str:
    bits: list[str] = []
    if line.lineup_probability is not None:
        bits.append(
            {1: "starter", 2: "likely starter", 3: "rotation"}.get(
                line.lineup_probability, "bench risk"
            )
        )
    if line.minutes_trend:
        bits.append(f"minutes {line.minutes_trend}")
    if line.is_dgw:
        bits.append("DOUBLE gameweek")
    return " · ".join(bits)


def _line_block(line: OfferLine) -> list[str]:
    """One offer, a few short lines. Position and club lead."""
    club = line.club or "unknown club"
    head = f"  {line.name} ({line.pos_short}, {club})  EUR {line.bid:,}"

    form: list[str] = []
    if line.season_avg is not None:
        form.append(f"avg {line.season_avg:.0f}/game")
    form.append(f"EP {line.ep:.0f}")
    if line.next_opponent:
        form.append(f"next {line.next_opponent}")

    price = f"MV {line.market_value:,} · bid {line.overbid_pct:+.1f}%"
    if line.trend_7d_pct is not None:
        price += f" · trend {line.trend_7d_pct:+.1f}%/7d"
        if line.is_falling:
            price += "  <-- FALLING"

    out = [head, f"      {' · '.join(form)}", f"      {price}"]

    availability = _availability(line)
    if availability:
        out.append(f"      {availability}")

    if line.fills_gap and line.position:
        out.append(f"      fills your {line.pos_short} gap")
    elif line.squad_at_position is not None and line.position_minimum is not None:
        out.append(
            f"      you have {line.squad_at_position} {line.pos_short} "
            f"(min {line.position_minimum})"
        )

    for risk in line.risks:
        out.append(f"      ! {risk}")
    return out


def render_session_board(
    *,
    squad_size: int,
    squad_cap: int,
    budget_before: int,
    budget_after: int,
    placed: list[OfferLine],
    refused: list[OfferLine],
) -> str:
    """What this session did with the wallet, in one message.

    `placed` are live offers; `refused` are the gate's and Kickbase's refusals,
    each with its reason on its own line so a ceiling that keeps firing is
    visible without a log query.
    """
    lines = [
        f"SQUAD {squad_size}/{squad_cap}   BUDGET EUR {budget_before:,} -> EUR {budget_after:,}",
        "",
    ]

    if placed:
        lines.append(f"OFFERS PLACED — {len(placed)}")
        for line in placed:
            lines += _line_block(line)
            lines.append("")
        lines.append(f"  total EUR {sum(line.bid for line in placed):,}")
    else:
        lines.append("OFFERS PLACED — none")

    if refused:
        lines += ["", f"REFUSED — {len(refused)}"]
        for line in refused:
            lines += _line_block(line)
            lines.append(f"      ! {line.detail}")
            lines.append("")

    return "\n".join(lines).rstrip()
```

- [ ] **Step 4: Delete the old board's tests and run the new ones**

```bash
git rm -q tests/test_proposal_overview.py
uv run pytest tests/test_session_board.py -q 2>&1 | tail -3
```

Expected: all PASS.

- [ ] **Step 5: Delete the overview senders**

In `rehoboam/notify/telegram.py` delete the function `overview_keyboard` (from `def overview_keyboard(` through `return {"inline_keyboard": rows}`) and the function `send_overview` (from `def send_overview(` through its `)` closing the `return _send_chunked(...)`). Leave `_approval_row`, `approval_keyboard`, `send_message`, `send_proposal`, `_send_chunked`, `_chunks` exactly as they are. Their only caller was `_send_proposal_overview`, which this task replaces; between this step and Step 16 the suite is red only on `auto_trader.py`'s stale imports, which the following steps remove. Run `uv run ruff check rehoboam/notify/telegram.py` — an unused import, if any appears, is removed.

- [ ] **Step 6: Write the failing execution tests**

Create `tests/test_plain_buys_execute.py`:

```python
"""Plain squad-improvement buys execute behind the safety gate (spec §1).

From 2026-08-24 to PR 1 these buys became Telegram proposals: 13 approvals,
2 acquisitions, the rest poached while the message waited. The session now
places the offer itself through `ExecutionService.buy` with a `BuyGate`, and
writes the case to `trade_proposals` AFTER the fact as executed / refused /
failed, so the board and the daily summary report what happened rather than
what was asked.

These drive the real `ExecutionService` and the real `LearningTracker`
against a mock API: what they assert is that `api.buy_player` is called (or
not), not that a stub recorded an argument.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rehoboam.auto_trader import AutoTrader, EPSessionContext, MatchdayPhase
from rehoboam.bid_learner import BidLearner
from rehoboam.config import Settings
from rehoboam.learning.tracker import LearningTracker
from rehoboam.services.execution import ExecutionService

LEAGUE = SimpleNamespace(id="1933872", name="PUMARUDEL")


@pytest.fixture
def api():
    api = MagicMock()
    api.buy_player = MagicMock(return_value=None)
    return api


@pytest.fixture
def trader(api, tmp_path, monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)
    t = AutoTrader(api=api, settings=Settings(), dry_run=False)
    t.learner = BidLearner(db_path=tmp_path / "bid_learning.db")
    t.tracker = LearningTracker(t.learner)
    t.execution = ExecutionService(api=api, tracker=t.tracker, dry_run=False)
    return t


@pytest.fixture(autouse=True)
def _trend(request):
    """A gentle uptrend unless a test asks (via indirect param) for another."""
    pct = getattr(request, "param", 1.9)
    with patch(
        "rehoboam.services.trend_service.TrendService.get_trend",
        return_value=SimpleNamespace(trend_7d_pct=pct),
    ):
        yield


def _player(pid="6080", price=32_285_629, team_id="2"):
    return SimpleNamespace(
        id=pid,
        first_name="Aleksandar",
        last_name="Pavlović",
        position="Midfielder",
        price=price,
        market_value=price,
        team_id=team_id,
        team_name="Bayern",
        average_points=119.0,
        status=0,
    )


def _rec(player=None, bid=32_608_485, ep_gain=57.2, sell_plan=None):
    player = player or _player()
    return SimpleNamespace(
        player=player,
        score=SimpleNamespace(
            expected_points=82.6, data_quality=None, position="Midfielder"
        ),
        marginal_ep_gain=ep_gain,
        recommended_bid=bid,
        replaces_player_name="Klaas",
        replaces_player_ep=25.5,
        roster_impact="upgrade",
        reason="upgrade",
        sell_plan=sell_plan,
    )


def _ctx(rec, budget=95_317_114, *, squad_size=13, phase="aggressive", days=5):
    squad = [
        SimpleNamespace(id=f"s{i}", team_id=f"club{i}", position="Defender")
        for i in range(squad_size)
    ]
    return EPSessionContext(
        ep_result={
            "buy_recs": [rec],
            "trade_pairs": [],
            "squad_scores": [],
            "market_players": {rec.player.id: rec.player},
        },
        matchday_phase=MatchdayPhase(
            days_until_match=days,
            phase=phase,
            max_trades=5,
            allow_flips=False,
            reason="test",
        ),
        my_bids=[],
        my_bid_amounts={},
        squad=squad,
        current_budget=budget,
        team_value=200_000_000,
        flip_budget=budget,
    )


class TestAnUpgradeBecomesAnOffer:
    def test_the_offer_reaches_the_api_at_the_recommended_bid(self, trader, api):
        rec = _rec()

        result = trader._execute_buy(LEAGUE, rec, _ctx(rec), free_slots=2)

        assert result.success
        assert api.buy_player.call_count == 1
        assert api.buy_player.call_args[0][1].id == "6080"
        assert api.buy_player.call_args[0][2] == 32_608_485

    def test_the_case_is_recorded_as_executed_after_the_fact(self, trader):
        rec = _rec()

        trader._execute_buy(LEAGUE, rec, _ctx(rec), free_slots=2)

        rows = trader.learner.proposals_since(0)
        assert [r["status"] for r in rows] == ["executed"]
        assert "Klaas" in rows[0]["message"] and "57.2" in rows[0]["message"]
        assert trader.learner.pending_proposals() == []

    def test_the_bid_is_tracked_as_pending_for_the_ledger(self, trader):
        rec = _rec()

        trader._execute_buy(LEAGUE, rec, _ctx(rec), free_slots=2)

        pending = trader.learner.get_pending_bids()
        assert [b["player_id"] for b in pending] == ["6080"]
        assert pending[0]["tier"] == "strong_upgrade"

    def test_the_session_counts_it(self, trader):
        rec = _rec()
        ctx = _ctx(rec)

        trader._execute_buy(LEAGUE, rec, ctx, free_slots=2)

        assert (ctx.offers_placed, ctx.offers_refused) == (1, 0)

    def test_the_board_carries_the_line(self, trader):
        rec = _rec()

        trader._execute_buy(LEAGUE, rec, _ctx(rec), free_slots=2)

        assert [line.outcome for line in trader._session_board] == ["placed"]
        assert trader._session_board[0].name == "Aleksandar Pavlović"


class TestTheGateStillDecides:
    def test_an_over_ceiling_bid_is_refused_and_recorded(self, trader, api):
        """+40% over market value against the strong tier's 25% ceiling."""
        rec = _rec(bid=45_200_000)
        ctx = _ctx(rec)

        result = trader._execute_buy(LEAGUE, rec, ctx, free_slots=2)

        assert not result.success
        assert api.buy_player.call_count == 0
        rows = trader.learner.proposals_since(0)
        assert [r["status"] for r in rows] == ["refused"]
        assert "REFUSED" in rows[0]["message"] and "overbid" in rows[0]["message"]
        assert (ctx.offers_placed, ctx.offers_refused) == (0, 1)
        assert trader._session_board[0].outcome == "refused"
        assert "overbid" in trader._session_board[0].detail

    def test_no_free_slot_is_refused(self, trader, api):
        rec = _rec()

        result = trader._execute_buy(
            LEAGUE, rec, _ctx(rec, squad_size=15), free_slots=0
        )

        assert not result.success
        assert api.buy_player.call_count == 0
        assert "no free squad slot" in trader._session_board[0].detail

    def test_a_kickbase_error_is_recorded_as_failed(self, trader, api):
        api.buy_player.side_effect = Exception(
            "Failed to make offer: 500 - UnderpayNotAllowed"
        )
        rec = _rec()
        ctx = _ctx(rec)

        result = trader._execute_buy(LEAGUE, rec, ctx, free_slots=2)

        assert not result.success
        assert [r["status"] for r in trader.learner.proposals_since(0)] == ["failed"]
        assert "UnderpayNotAllowed" in trader.learner.proposals_since(0)[0]["message"]
        assert (ctx.offers_placed, ctx.offers_refused) == (0, 1)
        assert trader._session_board[0].outcome == "failed"


class TestTheTrendFloorComesFirst:
    @pytest.mark.parametrize("_trend", [-27.0], indirect=True)
    def test_a_steeply_falling_player_is_never_attempted(self, trader, api):
        rec = _rec()

        assert trader._execute_buy(LEAGUE, rec, _ctx(rec), free_slots=2) is None
        assert api.buy_player.call_count == 0
        assert trader.learner.proposals_since(0) == []
        assert trader._session_board == []


class TestASellPlanRidesOnTheBid:
    def test_the_plan_is_persisted_with_the_pending_bid(self, trader, api):
        """Buy first, sell after: `resolve_auctions` runs the sells if we win."""
        plan = SimpleNamespace(
            players_to_sell=[
                SimpleNamespace(player_id="s1"),
                SimpleNamespace(player_id="s2"),
            ]
        )
        rec = _rec(sell_plan=plan)

        trader._execute_buy(LEAGUE, rec, _ctx(rec), free_slots=2)

        assert api.buy_player.call_count == 1
        assert trader.learner.get_pending_bids()[0]["sell_plan_player_ids"] == [
            "s1",
            "s2",
        ]


class TestDryRunSpendsAndRecordsNothing:
    def test_it_calls_no_api_writes_no_row_sends_nothing_but_shows_the_board(
        self, api, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.chdir(tmp_path)
        dry = AutoTrader(api=api, settings=Settings(), dry_run=True)
        dry.learner = BidLearner(db_path=tmp_path / "bid_learning.db")
        rec = _rec()
        ctx = _ctx(rec)

        with patch("rehoboam.notify.telegram.send_message") as send:
            result = dry._execute_buy(LEAGUE, rec, ctx, free_slots=2)
            dry._send_session_board(LEAGUE, ctx)
            send.assert_not_called()

        assert result.success
        assert api.buy_player.call_count == 0
        assert dry.learner.proposals_since(0) == []
        assert len(dry._session_board) == 1


class TestTheBoardIsSentOnce:
    def test_it_is_sent_when_an_offer_was_placed(self, trader):
        rec = _rec()
        ctx = _ctx(rec)
        trader._execute_buy(LEAGUE, rec, ctx, free_slots=2)

        with patch("rehoboam.notify.telegram.send_message", return_value=True) as send:
            trader._send_session_board(LEAGUE, ctx)

        send.assert_called_once()
        text = send.call_args[0][2]
        assert "OFFERS PLACED — 1" in text and "Pavlović" in text
        assert "Approve" not in text

    def test_it_is_sent_when_only_refusals_happened(self, trader):
        rec = _rec(bid=45_200_000)
        ctx = _ctx(rec)
        trader._execute_buy(LEAGUE, rec, ctx, free_slots=2)

        with patch("rehoboam.notify.telegram.send_message", return_value=True) as send:
            trader._send_session_board(LEAGUE, ctx)

        assert "REFUSED — 1" in send.call_args[0][2]

    def test_it_is_not_sent_when_nothing_was_attempted(self, trader):
        with patch("rehoboam.notify.telegram.send_message") as send:
            trader._send_session_board(LEAGUE, _ctx(_rec()))

        send.assert_not_called()


class TestTheUnifiedPhaseBuysInstead:
    def test_a_plain_buy_places_an_offer_and_takes_the_slot(self, trader, api):
        rec = _rec()
        ctx = _ctx(rec, squad_size=14)
        trader.api.get_squad.return_value = ctx.squad
        trader.api.get_my_bids.return_value = []
        trader.api.get_team_info.return_value = {
            "budget": ctx.current_budget,
            "team_value": ctx.team_value,
        }

        results = trader.run_unified_trade_phase(league=LEAGUE, ctx=ctx)

        assert api.buy_player.call_count == 1
        assert [r.action for r in results if r.success] == ["BUY"]
        assert ctx.executed_trade_count == 1
        assert ctx.offers_placed == 1
        assert ctx.current_budget == 95_317_114 - 32_608_485
        trader.api.sell_player_instant.assert_not_called()


class TestTheProposalMachineryIsGone:
    def test_nothing_proposes_any_more(self):
        for name in (
            "_propose_buy",
            "_has_pending_proposal",
            "_needs_sell_plan",
            "_send_proposal_overview",
        ):
            assert not hasattr(AutoTrader, name), name

    def test_execute_buy_takes_free_slots_and_no_deadline(self):
        params = inspect.signature(AutoTrader._execute_buy).parameters

        assert "free_slots" in params
        assert "auto_approve_at" not in params
```

Append to `tests/test_emergency_fill_every_phase.py` (it already imports `AutoTrader`, `Settings`, `patch`, and defines `LEAGUE`, `_squad_of_seven`, `_Api`, `_context`):

```python
class TestTheSessionReportsItsOffers:
    """`trades=0/0` could not tell a gated bot from a broken one (spec §1)."""

    def test_offers_placed_and_refused_reach_the_session_summary_and_the_log(
        self, tmp_path, monkeypatch, caplog
    ):
        import logging

        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.chdir(tmp_path)
        caplog.set_level(logging.INFO, logger="rehoboam.auto_trader")

        squad = _squad_of_seven()
        trader = AutoTrader(api=_Api(squad), settings=Settings(), dry_run=True)
        ctx = _context("moderate", 3, squad)
        ctx.offers_placed = 2
        ctx.offers_refused = 1

        with (
            patch.object(AutoTrader, "_build_session_context", return_value=ctx),
            patch.object(AutoTrader, "_run_emergency_squad_fill", return_value=[]),
            patch.object(AutoTrader, "run_profit_sell_phase", return_value=[]),
            patch.object(AutoTrader, "optimize_and_execute_squad", return_value=[]),
            patch.object(AutoTrader, "run_unified_trade_phase", return_value=[]),
            patch.object(AutoTrader, "_set_optimal_lineup", return_value=[]),
        ):
            session = trader.run_full_session(LEAGUE)

        assert (session.offers_placed, session.offers_refused) == (2, 1)
        assert "offers=2 refused=1" in caplog.text
```

- [ ] **Step 7: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_plain_buys_execute.py tests/test_emergency_fill_every_phase.py -q 2>&1 | tail -5`
Expected: `test_plain_buys_execute.py` fails on `AttributeError: … has no attribute '_execute_buy'` (or on `auto_trader.py`'s now-stale `ProposalLine` import); the new every-phase test fails on `EPSessionContext` having no `offers_placed`.

- [ ] **Step 8: The dataclasses and the small renames**

In `rehoboam/auto_trader.py`:

1. `AutoTradeSession` (~line 23): after `lineup: list[tuple[str, float, str | None]] = field(default_factory=list)` add

```python
    #: Offers the session placed itself, and candidates the gate or Kickbase
    #: refused (spec §1). `trades` counts executions; these count decisions.
    offers_placed: int = 0
    offers_refused: int = 0
```

2. `EPSessionContext` (~line 338): after `executed_trade_count: int = 0` add

```python
    offers_placed: int = 0
    offers_refused: int = 0
```

3. Rename `_is_too_falling_to_propose` → `_is_too_falling_to_buy` (definition ~line 154 and its one call inside the function that becomes `_execute_buy`). Keep its body and docstring; in the docstring replace the word "proposed" with "bought" if it appears.

1. In `__init__` (~line 389) replace

```python
        # REH-117: one message per session, not one per player. Collected here
        # and sent once by `_send_proposal_overview`.
        self._session_proposals: list = []
```

with

```python
        # One message per session, not one per offer (REH-117). Every attempted
        # buy appends an `OfferLine` here; `_send_session_board` sends it once.
        self._session_board: list = []
```

and in `run_full_session` (~line 1923) replace `self._session_proposals = []` with `self._session_board = []`.

- [ ] **Step 9: `_offer_line`**

Replace the function `_proposal_line` (~lines 184–226) with:

```python
def _offer_line(
    offer_id: str,
    rec,
    bid: int,
    trend: float | None,
    risks: list[str],
    *,
    outcome: str,
    detail: str,
):
    """Everything the board shows, from data the pipeline already has.

    `PlayerScore` carries position, lineup probability, minutes trend, average
    points and next opponent; `BuyRecommendation` carries the roster impact.
    The old per-player message discarded all of it and printed "unknown club"
    with no position, which is how a defender was proposed to a squad whose
    actual hole was at striker (REH-117).
    """
    from .config import POSITION_MINIMUMS
    from .notify.overview import OfferLine

    player = rec.player
    score = getattr(rec, "score", None)
    position = getattr(score, "position", "") or getattr(player, "position", "") or ""
    impact = getattr(rec, "roster_impact", "") or ""
    return OfferLine(
        offer_id=offer_id,
        name=f"{player.first_name} {player.last_name}".strip() or player.last_name,
        bid=int(bid),
        ep=float(getattr(score, "expected_points", 0.0) or 0.0),
        marginal_gain=float(getattr(rec, "marginal_ep_gain", 0.0) or 0.0),
        outcome=outcome,
        detail=detail,
        position=position,
        club=_club_name(player, score),
        market_value=int(getattr(player, "market_value", 0) or 0),
        fills_gap=impact == "fills_gap",
        trend_7d_pct=trend,
        season_avg=(
            float(score.average_points)
            if score is not None and getattr(score, "average_points", None) is not None
            else None
        ),
        lineup_probability=getattr(score, "lineup_probability", None),
        minutes_trend=getattr(score, "minutes_trend", None),
        next_opponent=getattr(score, "next_opponent", None),
        is_dgw=bool(getattr(score, "is_dgw", False)),
        position_minimum=POSITION_MINIMUMS.get(position),
        risks=tuple(risks),
    )
```

- [ ] **Step 10: `_execute_buy`**

Replace the whole method `_propose_buy` (from `def _propose_buy(self, league, rec, ctx, *, bid: int | None = None) -> bool:` through its final `return True`) with:

```python
def _execute_buy(self, league, rec, ctx, *, free_slots: int) -> AutoTradeResult | None:
    """Place the offer for a plain squad-improvement buy, and keep the case.

    Spec §1: one execution path. The trend floor still applies, the case is
    still rendered (`render_proposal` — now the record of what was done and
    why), and then `ExecutionService.buy` runs the safety gate and places
    the offer. The `trade_proposals` row is written AFTER the fact as
    'executed' / 'refused' / 'failed', so the board and the daily summary
    report what happened rather than what was asked.

    Returns None when the trend floor skipped the player (nothing was
    attempted), else the execution result — the caller appends it to the
    session's results so `trades=`, `total_spent` and the board see it.

    ``free_slots`` is the caller's count of open squad slots (open bids
    already deducted); the gate refuses a buy into a full squad.
    """
    import uuid

    from .notify.render import render_proposal
    from .services.bid_ceiling import tier_for_marginal_gain

    offer_id = uuid.uuid4().hex[:12]
    player = rec.player
    bid_amount = int(rec.recommended_bid)
    trend = None
    try:
        from .trader import Trader

        trend = (
            Trader(self.api, self.settings)
            .trend_service.get_trend(player.id, player.market_value, league.id)
            .trend_7d_pct
        )
    except Exception:
        logger.debug("buy: no trend for %s", player.id, exc_info=True)

    if _is_too_falling_to_buy(trend, self.settings):
        console.print(
            f"[dim]Skip {player.last_name} — market value falling {trend:.1f}%/7d[/dim]"
        )
        logger.info(
            "buy-skip player=%s trend=%.1f%% below %.1f%% floor",
            player.id,
            trend,
            float(self.settings.max_falling_trend_pct_to_buy),
        )
        return None

    risks: list[str] = []
    if getattr(rec.score, "data_quality", None) and rec.score.data_quality.grade != "A":
        risks.append(
            f"Data quality {rec.score.data_quality.grade} — no fitted history, "
            "scored on the position prior."
        )

    case = render_proposal(
        player_name=f"{player.first_name} {player.last_name}".strip(),
        club=getattr(player, "team_name", "") or "unknown club",
        bid=bid_amount,
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

    # A buy that only works by selling someone first carries its sell plan
    # on the bid; `resolve_auctions` runs those sells if we win. Buy first,
    # sell after — never sell before securing the player.
    sell_plan = getattr(rec, "sell_plan", None)
    sp_ids = (
        [entry.player_id for entry in sell_plan.players_to_sell]
        if sell_plan and getattr(sell_plan, "players_to_sell", None)
        else None
    )

    result = self.execution.buy(
        league,
        player,
        bid_amount,
        getattr(rec, "reason", "") or "EP upgrade",
        sell_plan_player_ids=sp_ids,
        current_budget=ctx.current_budget,
        days_until_match=ctx.matchday_phase.days_until_match,
        gate=_build_buy_gate(
            settings=self.settings,
            ctx=ctx,
            player=player,
            # The phase's allowance, not the wallet — see `BuyGate`.
            spendable_budget=int(ctx.flip_budget),
            free_slots=free_slots,
            marginal_ep_gain=rec.marginal_ep_gain,
        ),
    )

    gate_prefix = "safety gate refused: "
    if result.success:
        outcome, status, detail = "placed", "executed", ""
        ctx.offers_placed += 1
    elif (result.error or "").startswith(gate_prefix):
        outcome, status = "refused", "refused"
        detail = (result.error or "")[len(gate_prefix) :]
        ctx.offers_refused += 1
    else:
        outcome, status = "failed", "failed"
        detail = result.error or "unknown error"
        ctx.offers_refused += 1

    # Collected before the dry-run exit so `status` shows the board Marco
    # would receive rather than a line saying one exists.
    self._session_board.append(
        _offer_line(
            offer_id, rec, bid_amount, trend, risks, outcome=outcome, detail=detail
        )
    )
    if self.dry_run:
        # ExecutionService already printed DRY RUN; nothing is recorded.
        return result

    tier = tier_for_marginal_gain(
        float(rec.marginal_ep_gain),
        must_have=self.settings.bid_tier_must_have,
        strong=self.settings.bid_tier_strong_upgrade,
        solid=self.settings.bid_tier_solid_upgrade,
    )
    message = case if not detail else f"{case}\n\n{outcome.upper()}\n  {detail}"
    try:
        self.learner.record_proposal(
            proposal_id=offer_id,
            player_id=player.id,
            player_name=player.last_name,
            bid=bid_amount,
            market_value=int(player.market_value),
            message=message,
            tier=tier.value,
            batch_id=self._session_batch_id,
            status=status,
        )
    except Exception:
        logger.exception("buy: could not record %s", offer_id)

    logger.info(
        "offer %s id=%s player=%s bid=%d batch=%s%s",
        status,
        offer_id,
        player.id,
        bid_amount,
        self._session_batch_id,
        f" — {detail}" if detail else "",
    )
    return result
```

Make sure `AutoTradeResult` is importable in `auto_trader.py` (it already is — `from .services.execution import AutoTradeResult, ExecutionService` or equivalent; check the imports, add `AutoTradeResult` if only `ExecutionService` is imported).

- [ ] **Step 11: `_send_session_board`**

Replace the whole method `_send_proposal_overview` (from `def _send_proposal_overview(self, league, ctx) -> None:` through the closing `)` of its last `logger.warning(...)`) with:

```python
def _send_session_board(self, league, ctx) -> None:
    """Send what this session did with the wallet, as one message (spec §1).

    Once, at the end, after every offer has been placed or refused. Nothing
    here asks for a decision — there is no button — it is the record.
    Best-effort: a delivery failure must not fail the session; the rows in
    `trade_proposals` are the durable record and the daily summary reads
    them.
    """
    if not self._session_board:
        return

    from .notify.overview import render_session_board
    from .notify.telegram import send_message

    placed = [line for line in self._session_board if line.outcome == "placed"]
    refused = [line for line in self._session_board if line.outcome != "placed"]
    # `ctx.current_budget` was decremented by each placed offer, so the
    # session's own accounting recovers the opening figure exactly.
    budget_after = int(getattr(ctx, "current_budget", 0) or 0)
    budget_before = budget_after + sum(line.bid for line in placed)
    text = render_session_board(
        squad_size=len(getattr(ctx, "squad", []) or []),
        squad_cap=SQUAD_CAP,
        budget_before=budget_before,
        budget_after=budget_after,
        placed=placed,
        refused=refused,
    )
    console.print(text)
    if self.dry_run:
        console.print("[yellow]DRY RUN - board not sent[/yellow]")
        return

    delivered = send_message(
        self.settings.telegram_bot_token, self.settings.telegram_chat_id, text
    )
    logger.info(
        "session-board batch=%s placed=%d refused=%d spend=%d budget_after=%d delivered=%s",
        self._session_batch_id,
        len(placed),
        len(refused),
        sum(line.bid for line in placed),
        budget_after,
        delivered,
    )
    if not delivered:
        logger.warning(
            "session board %s not delivered; the trade_proposals rows are the record",
            self._session_batch_id,
        )
```

In `run_full_session`, change `self._send_proposal_overview(league, ctx)` to `self._send_session_board(league, ctx)`.

- [ ] **Step 12: Delete the proposal guards**

Delete the methods `_has_pending_proposal` (from `def _has_pending_proposal(` through its `return False` after the `except Exception:`) and `_needs_sell_plan` (the `@staticmethod` and its body) entirely.

- [ ] **Step 13: The unified phase buys**

In `run_unified_trade_phase`:

1. Delete the line `proposed_slots = 0  # slots reserved by proposals nobody has approved yet`.

1. In the `if kind == "buy":` branch, replace everything from `if self._has_pending_proposal(obj.player.id):` through the `continue` that follows the `if self._propose_buy(league, obj, ctx):` block (i.e. the two skip blocks and the propose block) with:

```python
                result = self._execute_buy(league, obj, ctx, free_slots=available_slots)
                if result is None:
                    continue  # trend floor — nothing attempted
                results.append(result)
                if result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += obj.recommended_bid
                    ctx.flip_budget -= obj.recommended_bid
                    ctx.current_budget -= obj.recommended_bid
                    # Kickbase counts an open offer toward the squad cap.
                    available_slots -= 1
                continue
```

3. In the `elif kind == "pair":` branch replace the comment block and guard

```python
                # Don't sell a player unnecessarily if there are open slots —
                # the same target should appear as a plain buy candidate instead.
                #
                # `proposed_slots` is added back deliberately. A proposal is not
                # a commitment: nobody has approved it and no money has moved.
                # Counting it as a filled slot would mean that merely PROPOSING
                # a buy is what switches on the autonomous sell-then-buy pair
                # path — the bot would start selling squad players off the back
                # of a decision Marco has not made yet.
                if available_slots + proposed_slots > 0:
                    continue
```

with

```python
                # Don't sell a player unnecessarily while there is an open slot —
                # the same target appears as a plain buy candidate instead. An
                # offer this session placed has taken its slot (Kickbase counts
                # open offers toward the cap), so pairs run once the squad is
                # full. PR 3's squad plan tightens this to "floor met and full".
                if available_slots > 0:
                    continue
```

Run `grep -n "proposed_slots\|_propose_buy\|_has_pending_proposal\|_needs_sell_plan\|_session_proposals\|_proposal_line\|_send_proposal_overview\|_is_too_falling_to_propose" rehoboam/` — expected: no hits.

- [ ] **Step 14: The counters in the summary and the log**

In `run_full_session`'s summary block, after the `console.print(f"Trades: …")` line add

```python
console.print(f"Offers: {ctx.offers_placed} placed, {ctx.offers_refused} refused")
```

Change the `logger.info("session-end …")` call to

```python
logger.info(
    "session-end duration=%.1fs phase=%s sells=%d trades=%d/%d offers=%d refused=%d "
    "spent=%d earned=%d net=%d errors=%d",
    end_time - start_time,
    ctx.matchday_phase.phase,
    len([r for r in sell_results if r.success and r.action == "SELL"]),
    len([r for r in trade_results if r.success]),
    len(trade_results),
    ctx.offers_placed,
    ctx.offers_refused,
    total_spent,
    total_earned,
    net_change,
    len(errors),
)
```

and add `offers_placed=ctx.offers_placed, offers_refused=ctx.offers_refused,` to the `return AutoTradeSession(...)` call (after `lineup=lineup,`).

- [ ] **Step 15: Repoint the four existing tests**

`tests/test_falling_mv_block.py`: change the import and every call from `_is_too_falling_to_propose` to `_is_too_falling_to_buy`; rename `test_everything_shallower_is_still_proposed` to `test_everything_shallower_is_still_bought`.

`tests/test_emergency_fill_executes.py`: in `test_it_never_proposes` change the monkeypatched attribute `"_propose_buy"` to `"_execute_buy"` and the failure message to `"the emergency fill must not route through the plain-buy path"`; replace `test_propose_buy_takes_no_deadline` with

```python
def test_propose_buy_is_gone(self):
    assert not hasattr(AutoTrader, "_propose_buy")
```

`tests/test_auto_trader_flip_switches.py`: rename `test_ep_buy_path_still_reaches_proposal_when_flip_buys_disabled` to `test_ep_buy_path_still_reaches_execution_when_flip_buys_disabled`, replace its docstring with

```python
"""The gate must disable only the flip block, not the surrounding
EP-driven buy/trade-pair loop it lives inside — pinning the failure
mode of a gate that accidentally disables more than the flip block.

The buy path now executes through `_execute_buy` (spec §1). It is
patched to return None so nothing is attempted; what belongs here is
that the path is REACHED with the right candidate. That reaching it
places an offer is pinned in test_plain_buys_execute.
"""
```

and replace the `with (...)` block and its assertions with

```python
        with (
            patch("rehoboam.trader.Trader.find_profit_opportunities") as mock_find,
            patch.object(AutoTrader, "_execute_buy", return_value=None) as mock_execute,
        ):
            results = trader.run_unified_trade_phase(league=SimpleNamespace(id="L"), ctx=ctx)

        mock_find.assert_not_called()
        assert results == []
        trader.api.buy_player.assert_not_called()
        mock_execute.assert_called_once()
        assert mock_execute.call_args[0][1].player.id == "p1"
        assert mock_execute.call_args[0][1].recommended_bid == 1_000_000
        assert mock_execute.call_args.kwargs["free_slots"] >= 1
```

Delete the old wiring test:

```bash
git rm -q tests/test_proposal_wiring.py
```

- [ ] **Step 16: Run the touched files, then everything**

Run: `uv run pytest tests/test_plain_buys_execute.py tests/test_session_board.py tests/test_emergency_fill_every_phase.py tests/test_emergency_fill_executes.py tests/test_auto_trader_flip_switches.py tests/test_falling_mv_block.py tests/test_proposal_store.py -q 2>&1 | tail -5`
Expected: all PASS.

Run: `uv run ruff check rehoboam/ tests/ --fix && uv run pytest -q 2>&1 | tail -3`
Expected: ruff clean; the whole suite green.

- [ ] **Step 17: Commit**

```bash
git add -A rehoboam/ tests/
git commit -F - <<'MSG'
feat(auto): plain buys execute behind the gate; the session board; offers=

`_propose_buy` becomes `_execute_buy`: same trend floor, same rendered
case, then `ExecutionService.buy` with a `BuyGate` — the path pairs and
the emergency fill already use (spec §1). The `trade_proposals` row is
written after the fact as executed / refused / failed with the reason in
its message. The board (`_send_session_board`) says what was done and
asks for nothing. `session-end` logs `offers=N refused=M` beside
`trades=`, so a gated bot and a broken one no longer look the same.

`ProposalLine` becomes `OfferLine` with an outcome and a detail;
`render_session_board` replaces the budget-split proposal overview and
`send_overview` / `overview_keyboard` go with it. `proposed_slots`,
`_has_pending_proposal` and `_needs_sell_plan` go with the proposal state
they served. The Telegram webhook is untouched.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UXD6K51WgvWan8PuqYbnxv
MSG
```

______________________________________________________________________

### Task 3: The summary stops saying "APPROVED"; CLAUDE.md; verification

**Files:**

- Modify: `deploy/azure_function/function_app.py` (`_send_daily_summary`, ~lines 161–181)
- Modify: `CLAUDE.md` (line 37 and line 40)

**Interfaces:** none new.

- [ ] **Step 1: The daily summary's labels**

In `deploy/azure_function/function_app.py`, `_send_daily_summary`, replace

```python
    executed += [
        f"APPROVED {p['player_name']} for EUR {int(p['bid']):,}"
        for p in resolved
        if p["status"] == "executed"
    ]
    blocked = list(session.errors) + [
        f"proposal for {p['player_name']} ended as {p['status']}"
        for p in resolved
        if p["status"] in {"failed", "rejected"}
    ]
```

with

```python
    # The session places its own offers now (spec §1); a row at 'executed'
    # means an offer went out, not that the player was won — PR 2b's ledger
    # will say which. 'refused' is the safety gate's answer.
    executed += [
        f"OFFERED {p['player_name']} at EUR {int(p['bid']):,}"
        for p in resolved
        if p["status"] == "executed"
    ]
    blocked = list(session.errors) + [
        f"offer for {p['player_name']} ended as {p['status']}"
        for p in resolved
        if p["status"] in {"failed", "rejected", "refused"}
    ]
```

Also update the comment immediately above that block (it begins `# A proposal Marco approved is executed by the webhook`) to:

```python
# Rows the session wrote after its own buys (executed / refused / failed),
# plus any pre-PR-2a proposal the webhook resolved. Without this the
# summary would never mention a EUR 32M offer the session placed.
```

- [ ] **Step 2: CLAUDE.md**

Replace line 37 (the bullet beginning `- **Approval gate (2026-08-24)**:`) with:

```markdown
- **Autonomous wallet (spec `docs/superpowers/specs/2026-09-05-autonomous-wallet-design.md`, rolling out Sep 2026)**: the Telegram approval gate of 2026-08-24 is gone. A plain squad-improvement buy is *executed* by the session — `_execute_buy` renders the case (`notify/render.py`), runs `ExecutionService.buy` behind `services/safety_gate`, and writes the row to `trade_proposals` **after the fact** as `executed` / `refused` / `failed`. The session board (`notify/overview.py`, one Telegram message, no buttons) is the record of what was done and why; `session-end` logs `offers=N refused=M`. The `telegram_approval` HTTP trigger stays deployed for rows that predate the change and for the directives of spec §4. **Everything else is autonomous too**: profit trading, trade pairs, emergency squad fill (executes in every phase since PR 1) and lineup setting.
```

On line 40 (the `status` bullet), replace the clause `` `_propose_buy` short-circuits on `dry_run` after rendering, so a diagnostic run neither writes a proposal row nor sends a Telegram message `` with `` `ExecutionService.buy` short-circuits on `dry_run` after the gate and `_execute_buy` records nothing in dry-run, so a diagnostic run neither places an offer, writes a `trade_proposals` row, nor sends the board ``.

Run: `uv run pre-commit run mdformat --files CLAUDE.md` (it may rewrap; that is fine).

- [ ] **Step 3: Full quality pass**

Run: `uv run ruff check rehoboam/ tests/ && uv run pytest -q 2>&1 | tail -2 && uv run mypy rehoboam/auto_trader.py rehoboam/notify/overview.py rehoboam/bid_learner.py --ignore-missing-imports 2>&1 | tail -3`
Expected: ruff clean; suite green; mypy reports no error on a line this PR added (`git blame` any it names; pre-existing ones are not this PR's).

- [ ] **Step 4: Live smoke against prod state (read-only)**

Run: `uv run rehoboam status 2>&1 | tail -60`
Then: `grep -E "session-start|session-context|buy-skip|offer (executed|refused|failed)|DRY RUN|OFFERS PLACED|REFUSED —|session-board|session-end" logs/rehoboam.log | tail -20`

Expected: `session-start … dry_run=True`; for each plain-buy candidate that reaches the phase either `DRY RUN: Trade not executed` from `ExecutionService` (the offer it *would* place) or a `Gate refused …` line; the board printed to the console with `OFFERS PLACED — N` (or `— none`) and, in dry-run, `DRY RUN - board not sent`; **no** `offer executed id=` line (dry-run records nothing); `session-end … offers=N refused=M … errors=0`; no traceback. Quote the `session-end` line and the board verbatim in the report.

- [ ] **Step 5: Commit**

```bash
git add deploy/azure_function/function_app.py CLAUDE.md
git commit -F - <<'MSG'
docs: the summary says OFFERED, and CLAUDE.md describes the wallet the bot runs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UXD6K51WgvWan8PuqYbnxv
MSG
```

- [ ] **Step 6: Hand back**

Report: commits, the smoke lines from Step 4, and any mypy line the PR introduced. Do not push and do not open the PR — the controller runs the final whole-branch review first, then pushes and opens the PR with base `marcobraun2013/pr1-bids-are-commitments` (or `main` if #103 has merged), titled `feat(auto): plain buys execute behind the gate; the session board (autonomous wallet PR 2a)`.
