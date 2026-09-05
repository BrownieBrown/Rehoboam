# PR 1 — Bids Are Commitments, Emergency Fill Executes: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the bot withdrawing its own placed bids on price, and make the emergency squad fill spend behind the safety gate instead of proposing with a 24h auto-approve.

**Architecture:** Two independent changes to the existing session pipeline. (1) `bid_evaluator.evaluate_active_bids` loses every price-based `CANCEL` branch and its ceiling helpers; the only cancel left is a bot-placed bid on a player whose Kickbase status makes him unfieldable (`config.UNAVAILABLE_STATUSES = {4, 256}`, the same set `h2h.py` already excludes from lineups). (2) `_run_emergency_squad_fill` calls `ExecutionService.buy` with a `BuyGate` — the code REH-114 replaced — and the auto-approve machinery (`_process_due_auto_approvals`, `BidLearner.due_auto_approvals`, `Settings.emergency_auto_approve_hours`, the `auto_approve_at` argument on `_propose_buy`) is deleted. Plain-buy proposals and the Telegram webhook are untouched: they are PR 2.

**Tech Stack:** Python 3.12 via `uv`; pytest; pydantic-settings; SQLite (`BidLearner`); Rich console; pre-commit (ruff, black, bandit, mdformat).

**Spec:** `docs/superpowers/specs/2026-09-05-autonomous-wallet-design.md` — §1 (emergency half), §2, Rollout item 1.

## Global Constraints

- Work on branch `marcobraun2013/pr1-bids-are-commitments`, created from `marcobraun2013/autonomous-wallet-spec` (which carries the spec). Never commit to `main`.
- Run everything through `uv`: `uv run pytest`, `uv run ruff check`, `uv run pre-commit run --files …`. Do not invoke `black` by hand on a file — the repo is not black-clean and whole-file formatting produces unrelated churn; the pre-commit hook handles staged changes at commit time.
- Every commit message ends with these two trailer lines:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01UXD6K51WgvWan8PuqYbnxv
  ```
- Kickbase status semantics: a player with `status` 4 (injured) or 256 (long-term injured) cannot be fielded. Any other value can. This is the repo's existing convention (`h2h.project_squad`, `config.py` line 269) and this plan does not widen it.
- The emergency fill must never relax the budget rule: `ExecutionService.buy` raises `BudgetSafetyError` in live mode when a buy within `LOCKOUT_DAYS` of kickoff would take the budget negative. The fill skips a candidate whose bid exceeds `budget_remaining` *before* calling `buy`, so that guard is never tripped by design.
- Tests run with `KICKBASE_EMAIL=test@example.com` / `KICKBASE_PASSWORD=test` set via `monkeypatch.setenv` and `monkeypatch.chdir(tmp_path)` so `BidLearner` writes under `tmp_path`.

______________________________________________________________________

## File Structure

| file                                                                                                             | responsibility after this PR                                                                                                                                                                                                         |
| ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `rehoboam/config.py`                                                                                             | gains `UNAVAILABLE_STATUSES = frozenset({4, 256})`; loses `Settings.emergency_auto_approve_hours`                                                                                                                                    |
| `rehoboam/h2h.py`                                                                                                | reads `UNAVAILABLE_STATUSES` instead of its private `OUT = {4, 256}`                                                                                                                                                                 |
| `rehoboam/bid_evaluator.py`                                                                                      | `evaluate_active_bids` keeps every bot bid unless the player is unfieldable; `untiered_price_ceiling`, `_price_ceiling` and the `for_profit` argument are deleted; `display_bid_evaluations` / `cancel_bad_bids` unchanged           |
| `rehoboam/auto_trader.py`                                                                                        | `_evaluate_open_bids` stops passing `for_profit`; `_process_due_auto_approvals` and its call site deleted; `_propose_buy` and `_proposal_line` lose `auto_approve_at`; `_run_emergency_squad_fill` executes via `self.execution.buy` |
| `rehoboam/bid_learner.py`                                                                                        | `due_auto_approvals` deleted (`record_proposal` keeps its optional `auto_approve_at` parameter — the column exists and PR 2 retires the whole proposal flow)                                                                         |
| `tests/test_bids_are_commitments.py`                                                                             | **new** — the evaluator contract, using the real Castello Jr. cancellation                                                                                                                                                           |
| `tests/test_tier_aware_bid_evaluation.py`                                                                        | ceiling-cancel class replaced by a hold-at-every-tier class; tier round-trip classes unchanged                                                                                                                                       |
| `tests/test_manual_bids_are_not_cancelled.py`                                                                    | `for_profit` dropped; "untiered bot bid is still evaluated" becomes "is held"                                                                                                                                                        |
| `tests/test_emergency_fill_executes.py`                                                                          | **new** — the fill buys, in every phase, behind the gate; the auto-approve machinery is gone                                                                                                                                         |
| `tests/test_emergency_fill_approval.py`                                                                          | **deleted** — it pinned the behaviour this PR removes                                                                                                                                                                                |
| `tests/test_min_hold_and_emergency_fill.py`, `tests/test_pacing_session.py`, `tests/test_autonomous_buy_gate.py` | the `_ProposalSpy` REH-114 introduced is removed; assertions return to `execution` / `api.buy_player`                                                                                                                                |

______________________________________________________________________

### Task 0: Branch

**Files:** none

- [ ] **Step 1: Create the working branch from the spec branch**

```bash
git checkout marcobraun2013/autonomous-wallet-spec
git checkout -b marcobraun2013/pr1-bids-are-commitments
git log --oneline -1
```

Expected: the last line shows `90afd28 docs(spec): the bot runs the wallet …`.

- [ ] **Step 2: Confirm the suite is green before touching anything**

Run: `uv run pytest -q -x 2>&1 | tail -3`
Expected: `… passed` with no failures. If anything fails here, stop — it is not this PR's problem, and the baseline must be green before the diff means anything.

______________________________________________________________________

### Task 1: The evaluator holds every bot bid; only an unfieldable player cancels

**Files:**

- Modify: `rehoboam/config.py` (module constants block, after `MAX_PLAYERS_PER_CLUB` at line 29)
- Modify: `rehoboam/h2h.py:165-176` (`project_squad`)
- Modify: `rehoboam/bid_evaluator.py:26-57` (delete `untiered_price_ceiling`), `:71-83` (delete `_price_ceiling`), `:85-263` (rewrite `evaluate_active_bids`)
- Modify: `rehoboam/auto_trader.py:571-614` (`_evaluate_open_bids`)
- Create: `tests/test_bids_are_commitments.py`
- Modify: `tests/test_tier_aware_bid_evaluation.py:74-104`
- Modify: `tests/test_manual_bids_are_not_cancelled.py`

**Interfaces:**

- Consumes: `MarketPlayer` (`rehoboam/kickbase_client.py:51`) with fields `id, first_name, last_name, market_value, status, user_offer_price`; `BidEvaluation` dataclass (`rehoboam/bid_evaluator.py:11`).

- Produces: `rehoboam.config.UNAVAILABLE_STATUSES: frozenset[int]`; `BidEvaluator.evaluate_active_bids(league, player_trends: dict | None = None, bid_tiers: dict[str, str] | None = None, bot_placed_ids: set[str] | None = None) -> list[BidEvaluation]` — **no `for_profit` parameter**. Task 2 does not depend on this task.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bids_are_commitments.py`:

```python
"""A placed bid is a commitment (autonomous-wallet spec §2).

Between 2026-08-30 and 2026-09-01 `bid_evaluator` withdrew five offers Marco
had approved, each re-judged against a ceiling the safety gate had already
enforced at placement:

    Castello Jr.  EUR 20,089,389  "39.4% over market value - above its ceiling"
    Harder        EUR  6,424,255  "34.4% over market value - above its ceiling"
    Kleindienst   EUR 24,748,891  "30.0% over market value - too expensive for flip"
    Badé          EUR 17,126,503  same reason, twice

Kleindienst and Castello were auctions the bot would have won. Price never
cancels. The one thing that does is the player becoming unfieldable.
"""

from __future__ import annotations

import inspect

import pytest

from rehoboam.bid_evaluator import BidEvaluator
from rehoboam.config import UNAVAILABLE_STATUSES, Settings
from rehoboam.kickbase_client import MarketPlayer
from rehoboam.services.bid_ceiling import Tier

LEAGUE = "1933872"


def _settings():
    return Settings(kickbase_email="test@example.com", kickbase_password="x")


def _offer(player_id, market_value, our_bid, *, status=0):
    return MarketPlayer(
        id=player_id,
        first_name="Lukeba",
        last_name="Castello Jr.",
        position="Defender",
        team_id="15",
        team_name="",
        price=market_value,
        market_value=market_value,
        points=0,
        average_points=60.0,
        status=status,
        seller_user_id=None,
        offer_count=1,
        user_offer_price=our_bid,
        user_offer_id="3616202",
    )


class _Api:
    def __init__(self, offers):
        self._offers = offers

    def get_my_bids(self, league):
        return list(self._offers)

    def get_market(self, league):
        return list(self._offers)


# The real 2026-09-01 20:00 cancellation: EUR 20,089,389 against a market
# value of EUR 14,411,000 — 39.4% over, past every tier's ceiling.
CASTELLO = _offer("7224", market_value=14_411_000, our_bid=20_089_389)


def _evaluate(offer, *, bot_placed_ids, bid_tiers=None, trends=None):
    evaluator = BidEvaluator(_Api([offer]), _settings())
    return evaluator.evaluate_active_bids(
        LEAGUE,
        player_trends=trends or {},
        bid_tiers=bid_tiers or {},
        bot_placed_ids=bot_placed_ids,
    )[0]


class TestPriceNeverCancels:
    def test_the_real_castello_bid_is_held(self):
        result = _evaluate(
            CASTELLO, bot_placed_ids={"7224"}, bid_tiers={"7224": Tier.MUST_HAVE.value}
        )

        assert result.recommendation == "KEEP", result.reason
        assert "held" in result.reason

    @pytest.mark.parametrize("tier", list(Tier))
    def test_every_tier_is_held_far_above_its_ceiling(self, tier):
        result = _evaluate(
            CASTELLO, bot_placed_ids={"7224"}, bid_tiers={"7224": tier.value}
        )

        assert result.recommendation == "KEEP", result.reason

    def test_an_untiered_bot_bid_is_held(self):
        """Rows predating the tier column: still the bot's, still a commitment."""
        result = _evaluate(CASTELLO, bot_placed_ids={"7224"})

        assert result.recommendation == "KEEP", result.reason

    def test_a_falling_trend_does_not_cancel(self):
        """Flip economics decide whether to PLACE a bid, never whether to pull one."""
        trends = {
            "7224": {"trend": "falling", "trend_pct": -30.0, "peak_value": 30_000_000}
        }

        result = _evaluate(CASTELLO, bot_placed_ids={"7224"}, trends=trends)

        assert result.recommendation == "KEEP", result.reason
        assert result.is_falling is True

    def test_the_reason_still_shows_the_premium(self):
        """The number is still worth seeing in the log — it just decides nothing."""
        result = _evaluate(
            CASTELLO, bot_placed_ids={"7224"}, bid_tiers={"7224": Tier.MUST_HAVE.value}
        )

        assert "+39.4%" in result.reason


class TestOnlyTheUnfieldableCancel:
    @pytest.mark.parametrize("status", sorted(UNAVAILABLE_STATUSES))
    def test_an_unavailable_player_cancels_a_bot_bid(self, status):
        offer = _offer(
            "7224", market_value=14_411_000, our_bid=20_089_389, status=status
        )

        result = _evaluate(offer, bot_placed_ids={"7224"})

        assert result.recommendation == "CANCEL"
        assert result.is_injured is True
        assert str(status) in result.reason

    @pytest.mark.parametrize("status", [1, 2])
    def test_a_status_the_lineup_would_still_field_does_not_cancel(self, status):
        """`h2h.project_squad` fields these; the evaluator must agree with the lineup."""
        offer = _offer(
            "7224", market_value=14_411_000, our_bid=20_089_389, status=status
        )

        assert _evaluate(offer, bot_placed_ids={"7224"}).recommendation == "KEEP"

    def test_an_unavailable_player_never_cancels_a_manual_bid(self):
        """Marco can see the injury and bid anyway (REH-115)."""
        offer = _offer("7224", market_value=14_411_000, our_bid=20_089_389, status=4)

        assert _evaluate(offer, bot_placed_ids=set()).recommendation == "KEEP"


class TestTheCeilingHelpersAreGone:
    def test_no_price_ceiling_survives_in_the_evaluator(self):
        import rehoboam.bid_evaluator as module

        assert not hasattr(module, "untiered_price_ceiling")
        assert not hasattr(BidEvaluator, "_price_ceiling")

    def test_for_profit_is_no_longer_an_argument(self):
        params = inspect.signature(BidEvaluator.evaluate_active_bids).parameters

        assert "for_profit" not in params


class TestTheLineupAndTheEvaluatorShareOneDefinition:
    def test_h2h_excludes_exactly_the_unavailable_statuses(self):
        from rehoboam.h2h import project_squad

        rows = [
            {"pn": "fit", "pos": 1, "ap": 50.0, "st": 0},
            {"pn": "knock", "pos": 2, "ap": 50.0, "st": 2},
            {"pn": "injured", "pos": 3, "ap": 99.0, "st": 4},
            {"pn": "long", "pos": 4, "ap": 99.0, "st": 256},
        ]

        fielded = {
            name for name, _position, _points in project_squad(rows, "me").eleven
        }

        assert fielded == {"fit", "knock"}
```

- [ ] **Step 2: Run the new file to verify it fails**

Run: `uv run pytest tests/test_bids_are_commitments.py -q 2>&1 | tail -5`
Expected: FAIL — `ImportError: cannot import name 'UNAVAILABLE_STATUSES' from 'rehoboam.config'`.

- [ ] **Step 3: Add the constant to `config.py` and use it in `h2h.py`**

In `rehoboam/config.py`, directly after the `MAX_PLAYERS_PER_CLUB = 3` block (line 29), add:

```python
# Kickbase `st` values under which a player cannot be fielded: injured (4)
# and long-term injured (256). The lineup projection (`h2h.py`) and the
# open-bid evaluator (`bid_evaluator.py`) both key on exactly this set — one
# definition, so a bid is never held on a player the lineup would refuse, nor
# cancelled on a status the lineup would still field.
UNAVAILABLE_STATUSES: frozenset[int] = frozenset({4, 256})
```

In `rehoboam/h2h.py`, inside `project_squad`, replace

```python
OUT = {4, 256}
```

with

```python
OUT = UNAVAILABLE_STATUSES
```

and add `UNAVAILABLE_STATUSES` to the module's imports: if `h2h.py` already has a `from rehoboam.config import …` line, append the name to it; otherwise add `from rehoboam.config import UNAVAILABLE_STATUSES` with the other project imports. Update the docstring sentence "(`st` 4 and 256)" to "(`UNAVAILABLE_STATUSES`)".

- [ ] **Step 4: Rewrite the evaluator**

In `rehoboam/bid_evaluator.py`:

1. Add `from rehoboam.config import UNAVAILABLE_STATUSES` to the imports.
1. Delete the module-level function `untiered_price_ceiling` (lines 26–57) entirely.
1. Delete the method `_price_ceiling` (lines 71–83) entirely.
1. Replace the whole `evaluate_active_bids` method (lines 85–263, from `def evaluate_active_bids(` to the `return evaluations` before `def display_bid_evaluations`) with:

```python
def evaluate_active_bids(
    self,
    league,
    player_trends: dict | None = None,
    bid_tiers: dict[str, str] | None = None,
    bot_placed_ids: set[str] | None = None,
) -> list[BidEvaluation]:
    """Re-read every live offer and decide which the bot may withdraw.

    A bid is a commitment. The safety gate enforced the ceiling once, at
    placement, against that moment's market value; re-judging it here as
    the value drifts is what withdrew Badé, Kleindienst, Harder and
    Castello Jr. between 2026-08-30 and 2026-09-01 — every one an offer
    Marco had approved, two of them auctions the bot would have won.
    Price therefore never cancels. Nor do flip economics: a falling trend
    or a thin appreciation estimate is a reason not to PLACE a bid, not a
    reason to pull one already competing.

    The one thing that cancels is the player: an offer on someone who can
    no longer be fielded (`UNAVAILABLE_STATUSES`) holds a slot and a
    budget for nothing.

    `get_my_bids` is `get_market` filtered by "do we hold an offer" and
    cannot say WHO placed it, so provenance comes from `pending_bids`: a
    recorded tier, or membership in ``bot_placed_ids``. An offer outside
    both was placed by hand and is never cancelled, injured or not —
    Marco can see the injury and bid anyway (REH-115).

    Args:
        league: League object
        player_trends: player_id -> trend dict. Only fills `is_falling`
            on the evaluation, for display; it decides nothing.
        bid_tiers: player_id -> the tier the bid was priced against.
        bot_placed_ids: player ids the bot has an open bid on. None falls
            back to `bid_tiers`, which errs toward leaving a human's bid
            alone.
    """
    bid_tiers = bid_tiers or {}
    bot_bids = set(bid_tiers)
    if bot_placed_ids is not None:
        bot_bids |= {str(pid) for pid in bot_placed_ids}

    evaluations: list[BidEvaluation] = []
    my_bids = self.api.get_my_bids(league)
    if not my_bids:
        return evaluations

    console.print(f"\n[cyan]📊 Evaluating {len(my_bids)} active bids...[/cyan]")

    for bid_player in my_bids:
        player_name = f"{bid_player.first_name} {bid_player.last_name}"
        our_bid = int(bid_player.user_offer_price or 0)
        market_value = int(bid_player.market_value or 0)
        trend = (player_trends or {}).get(bid_player.id, {})
        is_falling = trend.get("trend") == "falling"
        is_unavailable = int(bid_player.status or 0) in UNAVAILABLE_STATUSES
        bid_vs_mv_pct = (
            ((our_bid - market_value) / market_value) * 100 if market_value > 0 else 0.0
        )
        tier = bid_tiers.get(bid_player.id)
        ours_to_cancel = bid_player.id in bot_bids

        recommendation = "KEEP"
        if not ours_to_cancel:
            reason = "Placed manually — not the bot's bid to cancel"
        elif is_unavailable:
            recommendation = "CANCEL"
            reason = (
                f"Player unavailable (status {bid_player.status}) — cannot be fielded"
            )
        elif tier is not None:
            reason = (
                f"Priced as {tier} at placement — held to resolution "
                f"({bid_vs_mv_pct:+.1f}% vs market value)"
            )
        else:
            reason = (
                f"Bot bid — held to resolution ({bid_vs_mv_pct:+.1f}% vs market value)"
            )

        evaluations.append(
            BidEvaluation(
                player_id=bid_player.id,
                player_name=player_name,
                our_bid=our_bid,
                market_value=market_value,
                recommendation=recommendation,
                reason=reason,
                is_injured=is_unavailable,
                is_falling=is_falling,
            )
        )

    return evaluations
```

5. Update the module docstring at the top of `bid_evaluator.py` only if it describes price-based cancellation; leave `BidEvaluation`, `display_bid_evaluations` and `cancel_bad_bids` as they are.

- [ ] **Step 5: Drop `for_profit` at the one production call site**

Run: `grep -rn "evaluate_active_bids\|for_profit" rehoboam/`
The only production caller is `auto_trader._evaluate_open_bids` (around line 598). Remove the line `for_profit=True,` from that call. If the grep shows any other caller, remove `for_profit=` there as well.

Replace the docstring of `_evaluate_open_bids` (lines 572–584) with:

```python
"""Re-read every live offer and withdraw only the ones on unfieldable players.

`get_my_bids` is `get_market` filtered by "do we hold an offer", so it
returns offers Marco placed by hand alongside the bot's — and it cannot
say which is which. Provenance comes from `pending_bids`: a recorded
tier (REH-111) or plain membership (REH-115). A bid the bot placed is
a commitment — price never cancels it (spec §2); only a player who can
no longer be fielded does.

Both reads are best-effort: a learning-side failure must leave the
phase working, not silently resume cancelling Marco's bids, so the
provenance set falls back to the tiers rather than to None.
"""
```

- [ ] **Step 6: Bring the two existing evaluator test files to the new contract**

In `tests/test_tier_aware_bid_evaluation.py`:

Replace `_evaluate` (lines 74–78) with:

```python
def _evaluate(listing, tiers):
    evaluator = BidEvaluator(_FakeApi([listing]), _settings())
    return evaluator.evaluate_active_bids(LEAGUE, player_trends={}, bid_tiers=tiers)[0]
```

Replace the class `TestABidIsJudgedByItsOwnCeiling` (lines 81–104) with:

```python
class TestABidIsHeldWhateverItsCeiling:
    """REH-111 judged a bid by the tier it was priced under. The autonomous-
    wallet spec (§2) stops judging price after placement at all; the tier now
    matters for the ledger and the reason line, never for cancellation."""

    def test_a_must_have_bid_at_its_ceiling_is_kept(self):
        result = _evaluate(KLEINDIENST, {"849": Tier.MUST_HAVE.value})

        assert result.recommendation == "KEEP", result.reason

    def test_a_marginal_bid_above_its_ceiling_is_kept_too(self):
        """The 2026-08-30 Kleindienst cancellation, now a no-op at every tier."""
        result = _evaluate(KLEINDIENST, {"849": Tier.MARGINAL.value})

        assert result.recommendation == "KEEP", result.reason

    @pytest.mark.parametrize("tier", list(Tier))
    def test_the_tier_is_named_in_the_reason(self, tier):
        result = _evaluate(KLEINDIENST, {"849": tier.value})

        assert tier.value in result.reason
```

Leave `TestTheTierSurvivesTheRoundTrip` and `TestTheApprovedBidRecordsItsTier` untouched.

In `tests/test_manual_bids_are_not_cancelled.py`:

1. In `_evaluate` (lines 74–82) delete the line `for_profit=True,`.
1. In `test_a_manual_bid_on_an_injured_player_is_still_kept` change `status=1` to `status=4` (status 1 is fieldable under the shared definition, so the test would no longer exercise the injury rule).
1. Replace the class `TestTheBotStillPolicesItsOwnBids` with:

```python
class TestTheBotHoldsItsOwnBids:
    def test_an_untiered_bot_bid_is_held(self):
        """Provenance decides whether the bot MAY cancel; price never says it should."""
        result = _evaluate(HARDER, bot_placed_ids={"9999"})

        assert result.recommendation == "KEEP", result.reason

    def test_a_tiered_bot_bid_at_its_ceiling_is_kept(self):
        result = _evaluate(
            HARDER, bot_placed_ids={"9999"}, bid_tiers={"9999": "must_have"}
        )

        assert result.recommendation == "KEEP", result.reason
```

4. In `TestTheDefaultIsSafe.test_omitting_the_set_does_not_silently_cancel_manual_bids` change the call to `evaluator.evaluate_active_bids(LEAGUE, player_trends={})[0]` (drop `for_profit=True`).
1. In the module docstring, the sentence mentioning `untiered_price_ceiling` is history and stays.

- [ ] **Step 7: Run the three evaluator test files and the session-wiring test**

Run: `uv run pytest tests/test_bids_are_commitments.py tests/test_tier_aware_bid_evaluation.py tests/test_manual_bids_are_not_cancelled.py tests/test_high_bidder_losses.py -q 2>&1 | tail -5`
Expected: all PASS. `Projection.eleven` is `list[tuple[name, position, points]]` (`rehoboam/h2h.py:62`), which is what the shared-definition test reads.

- [ ] **Step 8: Lint and run the whole suite**

Run: `uv run ruff check rehoboam/ tests/ --fix && uv run pytest -q 2>&1 | tail -3`
Expected: ruff clean; suite green.

- [ ] **Step 9: Commit**

```bash
git add rehoboam/config.py rehoboam/h2h.py rehoboam/bid_evaluator.py rehoboam/auto_trader.py \
        tests/test_bids_are_commitments.py tests/test_tier_aware_bid_evaluation.py \
        tests/test_manual_bids_are_not_cancelled.py
git commit -F - <<'MSG'
fix(bidding): a placed bid is a commitment — price never cancels it

`bid_evaluator` re-judged every bot bid against its tier ceiling each
session as the market value drifted. Between 2026-08-30 and 2026-09-01
that withdrew five offers Marco had approved (Badé x2, Kleindienst,
Harder, Castello Jr.); Kleindienst and Castello were auctions the bot
would have won. The gate enforces the ceiling once, at placement.

The only cancel left is a bot bid on a player who can no longer be
fielded — `UNAVAILABLE_STATUSES` {4, 256}, now one definition shared
with the lineup projection in `h2h.py`. Manual bids stay untouchable.

Spec: docs/superpowers/specs/2026-09-05-autonomous-wallet-design.md §2

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UXD6K51WgvWan8PuqYbnxv
MSG
```

______________________________________________________________________

### Task 2: The emergency fill spends behind the gate; the auto-approve machinery goes

**Files:**

- Modify: `rehoboam/auto_trader.py:183-226` (`_proposal_line`), `:616-670` (delete `_process_due_auto_approvals`), `:672-796` (`_propose_buy` signature + two call lines), `:1550-1615` (the tail of `_run_emergency_squad_fill`), `:2017-2020` (the call site in `run_full_session`)
- Modify: `rehoboam/bid_learner.py:1967-1990` (delete `due_auto_approvals`)
- Modify: `rehoboam/config.py:470-480` (delete `emergency_auto_approve_hours`)
- Delete: `tests/test_emergency_fill_approval.py`
- Create: `tests/test_emergency_fill_executes.py`
- Modify: `tests/test_min_hold_and_emergency_fill.py`, `tests/test_pacing_session.py`, `tests/test_autonomous_buy_gate.py`

**Interfaces:**

- Consumes: `ExecutionService.buy(league, player, price, reason, sell_plan_player_ids=None, *, current_budget, days_until_match, gate) -> AutoTradeResult` (`rehoboam/services/execution.py:75`); `_build_buy_gate(*, settings, ctx, player, spendable_budget, free_slots, marginal_ep_gain, released_player_id=None) -> BuyGate` (`rehoboam/auto_trader.py:60`); `select_emergency_basket` and the `attempts` list already built above the replaced block.

- Produces: `AutoTrader._propose_buy(self, league, rec, ctx, *, bid: int | None = None) -> bool`; `_proposal_line(proposal_id, rec, bid, trend, risks)`; `_run_emergency_squad_fill` returning `AutoTradeResult`s with `action="BUY"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_emergency_fill_executes.py`:

```python
"""The emergency fill spends, in every phase, behind the safety gate (spec §1).

REH-114 turned the fill into a proposal with a 24h auto-approve. Checked on a
12h timer, the deadline fired 24-36h later — El-Faouzi's on 2026-09-02 08:00,
to "no longer on the market". An empty lineup slot is -100 every matchday and
a proposal nobody taps protects nothing, so the fill buys, and `BuyGate` is
the only thing between the pick and the money.

These drive the real `ExecutionService` against a mock API: what they assert
is that `api.buy_player` is called, not that a stub recorded an argument.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rehoboam.auto_trader import AutoTrader
from rehoboam.bid_learner import BidLearner
from rehoboam.config import Settings
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
    t.execution = ExecutionService(api=api, tracker=MagicMock(), dry_run=False)
    return t


def _player(pid, position="Forward", price=1_000_000, team_id="1"):
    return SimpleNamespace(
        id=pid,
        first_name="X",
        last_name=f"P{pid}",
        position=position,
        price=price,
        market_value=price,
        average_points=10.0,
        status=0,
        team_id=team_id,
    )


def _rec(player, bid, ep_gain=10.0):
    return SimpleNamespace(
        player=player, recommended_bid=bid, marginal_ep_gain=ep_gain, sell_plan=None
    )


def _ctx(
    buy_recs, current_budget, *, squad=(), phase="moderate", days_until_match=None
):
    return SimpleNamespace(
        ep_result={
            "buy_recs": list(buy_recs),
            "trade_pairs": [],
            "squad_scores": [],
            "market_players": {r.player.id: r.player for r in buy_recs},
        },
        my_bid_amounts={},
        my_bids=[],
        squad=list(squad),
        current_budget=current_budget,
        team_value=100_000_000,
        flip_budget=current_budget,
        executed_trade_count=0,
        matchday_phase=SimpleNamespace(days_until_match=days_until_match, phase=phase),
    )


def _short_squad():
    """Ten defenders from ten clubs: one slot short, no club-limit noise."""
    return [_player(f"d{i}", "Defender", team_id=f"club{i}") for i in range(10)]


class TestTheFillBuys:
    def test_a_pick_reaches_the_api_at_the_basket_price(self, trader, api):
        target = _player("f1", price=4_000_000)
        squad = _short_squad()

        results = trader._run_emergency_squad_fill(
            league=LEAGUE,
            ctx=_ctx([_rec(target, 4_000_000)], 50_000_000, squad=squad),
            fresh_squad=squad,
            slots_short=1,
        )

        assert api.buy_player.call_count == 1
        assert api.buy_player.call_args[0][1].id == "f1"
        assert api.buy_player.call_args[0][2] == 4_000_000
        assert [r.action for r in results if r.success] == ["BUY"]

    def test_it_never_proposes(self, trader, api, monkeypatch):
        monkeypatch.setattr(
            AutoTrader,
            "_propose_buy",
            lambda *a, **k: pytest.fail("the emergency fill must spend, not ask"),
        )
        target = _player("f1", price=4_000_000)
        squad = _short_squad()

        trader._run_emergency_squad_fill(
            league=LEAGUE,
            ctx=_ctx([_rec(target, 4_000_000)], 50_000_000, squad=squad),
            fresh_squad=squad,
            slots_short=1,
        )

        assert api.buy_player.call_count == 1

    def test_it_buys_in_the_locked_phase(self, trader, api):
        """Locked blocks trading, not the fill: -100 per slot outranks it."""
        target = _player("f1", price=4_000_000)
        squad = _short_squad()
        ctx = _ctx(
            [_rec(target, 4_000_000)],
            50_000_000,
            squad=squad,
            phase="locked",
            days_until_match=1,
        )

        trader._run_emergency_squad_fill(
            league=LEAGUE, ctx=ctx, fresh_squad=squad, slots_short=1
        )

        assert api.buy_player.call_count == 1

    def test_the_spend_is_counted_against_the_daily_limit(self, trader, api):
        target = _player("f1", price=4_000_000)
        squad = _short_squad()
        before = trader.daily_spend

        trader._run_emergency_squad_fill(
            league=LEAGUE,
            ctx=_ctx([_rec(target, 4_000_000)], 50_000_000, squad=squad),
            fresh_squad=squad,
            slots_short=1,
        )

        assert trader.daily_spend == before + 4_000_000

    def test_the_second_pick_sees_what_the_first_one_spent(self, trader, api):
        """EUR 60m of picks against EUR 50m: one buy, never a negative budget."""
        a = _player("a", price=30_000_000)
        b = _player("b", price=30_000_000)
        squad = _short_squad()[:9]
        ctx = _ctx(
            [_rec(a, 30_000_000, ep_gain=20.0), _rec(b, 30_000_000, ep_gain=15.0)],
            50_000_000,
            squad=squad,
        )

        trader._run_emergency_squad_fill(
            league=LEAGUE, ctx=ctx, fresh_squad=squad, slots_short=2
        )

        assert api.buy_player.call_count == 1


class TestTheAutoApproveMachineryIsGone:
    def test_no_deadline_setting(self, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")

        assert not hasattr(Settings(), "emergency_auto_approve_hours")

    def test_no_due_auto_approvals_reader(self, tmp_path):
        assert not hasattr(BidLearner(db_path=tmp_path / "b.db"), "due_auto_approvals")

    def test_the_session_has_no_auto_approval_step(self):
        assert not hasattr(AutoTrader, "_process_due_auto_approvals")

    def test_propose_buy_takes_no_deadline(self):
        params = inspect.signature(AutoTrader._propose_buy).parameters

        assert "auto_approve_at" not in params
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_emergency_fill_executes.py -q 2>&1 | tail -8`
Expected: the `TestTheFillBuys` tests FAIL with `api.buy_player.call_count == 0` (or the `pytest.fail` from `_propose_buy`); the `TestTheAutoApproveMachineryIsGone` tests FAIL on `hasattr`.

- [ ] **Step 3: Delete the auto-approve machinery**

`rehoboam/config.py`: delete the whole `emergency_auto_approve_hours: float = Field(...)` block (lines 470–480, from `emergency_auto_approve_hours` through its closing `)`).

`rehoboam/bid_learner.py`: delete the method `due_auto_approvals` (lines 1967–1990, from `def due_auto_approvals(self, *, now: float)` through `return [dict(r) for r in rows]`). Leave `record_proposal`'s `auto_approve_at: float | None = None` parameter and the column migration in place.

`rehoboam/auto_trader.py`:

1. Delete the method `_process_due_auto_approvals` (lines 616–670, from `def _process_due_auto_approvals(self, league) -> None:` through the closing `)` of its last `logger.warning(...)`).
1. In `run_full_session`, delete these four lines (around 2017–2020):

```python
        # REH-114: an emergency proposal that nobody actioned becomes a buy once
        # its deadline passes. Runs before the emergency check below so a slot
        # already paid for is not proposed a second time.
        self._process_due_auto_approvals(league)
```

3. Change `_proposal_line`'s signature (line 183) to

```python
def _proposal_line(proposal_id: str, rec, bid: int, trend: float | None, risks: list[str]):
```

and its `is_emergency=auto_approve_at is not None,` line to

```python
        # The emergency fill executes (spec §1); nothing proposed is an emergency.
        is_emergency=False,
```

4. Change `_propose_buy`'s signature (lines 672–674) to

```python
    def _propose_buy(self, league, rec, ctx, *, bid: int | None = None) -> bool:
```

Delete the docstring paragraph beginning ```  ``auto_approve_at`` stamps a deadline ``` through `An ordinary upgrade waits indefinitely.`. Change the call `_proposal_line(proposal_id, rec, bid_amount, trend, risks, auto_approve_at)` to `_proposal_line(proposal_id, rec, bid_amount, trend, risks)`. In the `self.learner.record_proposal(...)` call delete the line `auto_approve_at=auto_approve_at,`.

- [ ] **Step 4: Restore execution in the emergency fill**

In `_run_emergency_squad_fill`, replace everything from the comment line `# REH-114: propose rather than spend.` down to and including the `logger.info("emergency-proposals …")` call (the block ends just before `return results`) with:

```python
        # An empty slot is -100 at kickoff whether or not anyone is watching,
        # so the fill spends (spec §1). REH-114 made it propose with a 24h
        # auto-approve; checked on a 12h timer that fired 24-36h later, and
        # El-Faouzi was gone by then. The gate is the only thing between a
        # pick and the money, and a refusal means "try the next candidate",
        # not "field nobody". What the loop must NOT do is relax the budget
        # rule: a negative budget at kickoff is zero points for the entire
        # matchday, far worse than -100.
        bought = 0
        for rec, bid in attempts:
            if bought >= slots_short:
                break
            if bid > budget_remaining:
                continue

            result = self.execution.buy(
                league,
                rec.player,
                bid,
                f"Emergency lineup fill (squad short by {slots_short})",
                current_budget=budget_remaining,
                days_until_match=ctx.matchday_phase.days_until_match,
                gate=_build_buy_gate(
                    settings=self.settings,
                    ctx=ctx,
                    player=rec.player,
                    spendable_budget=budget_remaining,
                    free_slots=slots_short - bought,
                    marginal_ep_gain=rec.marginal_ep_gain,
                ),
            )
            results.append(result)
            if not result.success:
                continue
            bought += 1
            # The next pick sees what this one spent, so a basket cannot
            # assume the whole wallet twice.
            budget_remaining -= bid
            self.daily_spend += bid
            gap_positions.discard(rec.player.position)

        console.print(
            f"[green]✓ Emergency fill: bought {bought}/{slots_short} player(s)[/green]"
        )
        logger.info(
            "emergency-fill slots_short=%d bought=%d spend=%d",
            slots_short,
            bought,
            sum(r.price for r in results if r.success),
        )
```

Also delete the now-unused local `deadline = time.time() + …` line if it survived above the replaced block, and remove the `import time` at the top of `auto_trader.py` **only** if `grep -n "time\." rehoboam/auto_trader.py` shows no remaining use (it will — `time.time()` is used elsewhere; leave it).

- [ ] **Step 5: Run the new file to verify it passes**

Run: `uv run pytest tests/test_emergency_fill_executes.py -q 2>&1 | tail -3`
Expected: all PASS.

- [ ] **Step 6: Delete the approval-era test file and repoint the three spied tests**

```bash
git rm tests/test_emergency_fill_approval.py
```

`tests/test_min_hold_and_emergency_fill.py`: replace the `_ProposalSpy` class with

```python
class _StubExecution:
    """Records every buy/sell call without hitting the API."""

    def __init__(self):
        self.calls: list = []

    def buy(
        self,
        league,
        player,
        price,
        reason,
        sell_plan_player_ids=None,
        *,
        current_budget=None,
        days_until_match=None,
        gate=None,
    ):
        self.calls.append(("buy", player.id, price, reason))
        return SimpleNamespace(
            success=True,
            player_name=player.last_name,
            action="BUY",
            price=price,
            reason=reason,
            timestamp=time.time(),
            error=None,
        )

    def instant_sell(self, league, player, reason):
        self.calls.append(("sell", player.id, player.market_value, reason))
        return SimpleNamespace(
            success=True,
            player_name=player.last_name,
            action="SELL",
            price=player.market_value,
            reason=reason,
            timestamp=time.time(),
            error=None,
        )
```

(make sure `import time` and `from types import SimpleNamespace` are among the file's imports), then in every `TestEmergencySquadFill` test replace `trader._propose_buy = spy = _ProposalSpy()` with `trader.execution = _StubExecution()` and the assertions as follows — insert `bought_ids = [pid for kind, pid, *_ in trader.execution.calls if kind == "buy"]` before each:

| was                                            | becomes                               |
| ---------------------------------------------- | ------------------------------------- |
| `assert len(spy.ids) == 2`                     | `assert len(bought_ids) == 2`         |
| `assert spy.ids == ["forward2"]` (three tests) | `assert bought_ids == ["forward2"]`   |
| `assert spy.ids[0] == "fwd_gap"`               | `assert bought_ids[0] == "fwd_gap"`   |
| `assert spy.ids == []`                         | `assert trader.execution.calls == []` |

Keep the REH-114 additions to `_ctx` (`market_players`, `my_bids`, `squad`, `team_value`, `phase`) — the gate needs them.

`tests/test_pacing_session.py`: delete the `_ProposalSpy` class. In `test_emergency_fill_buys_when_bid_is_already_sized` delete `trader._propose_buy = spy = _ProposalSpy()` and replace the two `spy` assertions with

```python
    assert api.buy_player.call_count == 1
    assert api.buy_player.call_args[0][2] == 4_000_000
```

In `test_emergency_fill_is_not_starved_by_a_paced_zero_bid` delete the spy line and replace its assertions with

```python
    assert api.buy_player.call_count == 1
    # The fallback must actually place an offer -- at the asking price, not 0 --
    # and still respect the budget the emergency path is working with.
    assert api.buy_player.call_args[0][2] == target.price
    assert api.buy_player.call_args[0][2] <= ctx.current_budget
```

`tests/test_autonomous_buy_gate.py`: delete the `_ProposalSpy` class. In `TestEmergencyFillRefusal.test_over_ceiling_candidate_is_skipped_and_the_next_one_is_bought` delete `trader._propose_buy = spy = _ProposalSpy()` and replace the two `spy`/`call_count == 0` assertions with

```python
        bought = [c.args[1].id for c in api.buy_player.call_args_list]
        assert bought == ["clean"], "the refused candidate must not reach the API"
```

keeping `assert any(r.success for r in results), "the slot must still be filled"`.

- [ ] **Step 7: Run every file this task touched, then the whole suite**

Run: `uv run pytest tests/test_emergency_fill_executes.py tests/test_min_hold_and_emergency_fill.py tests/test_pacing_session.py tests/test_autonomous_buy_gate.py tests/test_emergency_fill_every_phase.py tests/test_emergency_basket.py tests/test_proposal_wiring.py tests/test_auto_trader_flip_switches.py -q 2>&1 | tail -5`
Expected: all PASS.

Run: `uv run ruff check rehoboam/ tests/ --fix && uv run pytest -q 2>&1 | tail -3`
Expected: ruff clean; suite green. A remaining reference to `auto_approve_at`, `due_auto_approvals` or `emergency_auto_approve_hours` anywhere shows up here — `grep -rn "auto_approve\|emergency_auto_approve_hours" rehoboam/ tests/ deploy/` must return only `bid_learner.py`'s `record_proposal` parameter and migration.

- [ ] **Step 8: Commit**

```bash
git add -A rehoboam/ tests/
git commit -F - <<'MSG'
feat(emergency): the squad fill spends behind the gate, no auto-approve

REH-114 made the emergency fill propose with a 24h auto-approve. The
deadline is checked on a 12h timer, so it fired 24-36h later —
El-Faouzi's on 2026-09-02 08:00, to "no longer on the market". An empty
slot is -100 every matchday and a proposal nobody taps protects nothing.

The fill now calls `ExecutionService.buy` with a `BuyGate` in every
phase, as it did before REH-114. `_process_due_auto_approvals`,
`BidLearner.due_auto_approvals` and `Settings.emergency_auto_approve_hours`
are gone; `_propose_buy` no longer takes a deadline. Plain-buy proposals
and the Telegram webhook are untouched (PR 2).

Spec: docs/superpowers/specs/2026-09-05-autonomous-wallet-design.md §1

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UXD6K51WgvWan8PuqYbnxv
MSG
```

______________________________________________________________________

### Task 3: Verification against prod state, PR

**Files:** none new. Reads `logs/rehoboam.log`.

- [ ] **Step 1: Full quality pass**

Run: `uv run ruff check rehoboam/ tests/ && uv run mypy rehoboam/ --ignore-missing-imports 2>&1 | tail -3 && uv run bandit -r rehoboam/ -c pyproject.toml -q 2>&1 | tail -3`
Expected: ruff clean; mypy reports no *new* errors in `bid_evaluator.py` / `auto_trader.py` (compare against `git stash; …; git stash pop` if unsure); bandit clean.

- [ ] **Step 2: Live smoke against prod state (read-only)**

Per the project rule, an API-touching change is smoke-tested against the real account before merge. `rehoboam status` logs in, runs the whole session in dry-run and spends nothing.

Run: `uv run rehoboam status 2>&1 | tail -40`

Then: `grep -E "Evaluating|Keep:|Cancel:|held to resolution|Placed manually|emergency-fill|LINEUP EMERGENCY|session-end" logs/rehoboam.log | tail -15`

Expected: if any offer is open, every evaluation line says `held to resolution` or `Placed manually` and `Cancel: 0`; `session-end … errors=0`. If the squad is short, an `emergency-fill slots_short=N bought=…` line (dry-run reports what it would buy). No traceback.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin marcobraun2013/pr1-bids-are-commitments
gh pr create --title "fix(bidding): bids are commitments; emergency fill executes (autonomous wallet PR 1)" --body-file - <<'BODY'
## What

PR 1 of the autonomous-wallet design (`docs/superpowers/specs/2026-09-05-autonomous-wallet-design.md`, included here with its plan).

- `bid_evaluator` no longer cancels a bot-placed bid on price. Between 2026-08-30 and 2026-09-01 it withdrew five approved offers (Badé x2, Kleindienst, Harder, Castello Jr.) by re-judging them against the tier ceiling as the market value drifted; two were auctions the bot would have won. The only cancel left is a bid on a player who can no longer be fielded — `UNAVAILABLE_STATUSES` {4, 256}, now one definition shared with `h2h.py`.
- The emergency squad fill spends again, through `ExecutionService.buy` with a `BuyGate`, in every phase. REH-114's 24h auto-approve fired 24-36h later on a 12h timer (El-Faouzi, 2026-09-02, "no longer on the market"). `_process_due_auto_approvals`, `due_auto_approvals`, `emergency_auto_approve_hours` are deleted.

Plain-buy proposals and the Telegram webhook are untouched — PR 2.

## Verification

- `uv run pytest` green; `tests/test_bids_are_commitments.py` and `tests/test_emergency_fill_executes.py` pin the new contract with the real Castello / El-Faouzi cases.
- `rehoboam status` against prod state: every open offer `held to resolution`, `Cancel: 0`, `errors=0`.

## After merge

CI deploys to `func-rehoboam`. Confirm on the next session in App Insights: `Cancel: 0` on the evaluation summary, and `emergency-fill` (not `emergency-proposals`) if the squad is short.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01UXD6K51WgvWan8PuqYbnxv
BODY
```

- [ ] **Step 4: Hand back**

Report to Marco: PR URL, the smoke-test lines from Step 2, and that merge + deploy is his call. Do not merge.
