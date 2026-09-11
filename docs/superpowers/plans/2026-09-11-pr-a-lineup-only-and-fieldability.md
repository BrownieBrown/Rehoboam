# PR A: lineups only, and fieldability with ceilings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `trading_mode=lineup_only` switch that stops every sell and buy phase except the emergency fill, and a fieldability model that knows Kickbase's legal formations, so a squad like 2026-09-11's (1 GK, 6 DEF, 3 MID, 1 FW) is recognised as one player short, the emergency fill buys a midfielder or forward instead of a seventh defender, and the lineup step never submits ten names.

**Architecture:** `formation.py` gains the legal formation set as data and a pure `fieldability_from_counts` that answers "how many purchases, at which positions, make some legal formation fit"; `select_best_eleven` becomes formation-aware with the old greedy as a fallback for squads that cannot field eleven. `services/emergency_basket.py` gains an optional `gap_after` callback so the basket is scored by how many slots it actually closes. `auto_trader.py` wires both in, refuses to submit an illegal lineup, and gates steps 4–7 on `Settings.trading_mode`. Deploy plumbing carries `TRADING_MODE` through Bicep so an infra deploy cannot wipe it.

**Tech Stack:** Python 3.10+ (CI matrix 3.10/3.11/3.12), pydantic-settings, pytest, Azure Functions (Python 3.11), Bicep. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-11-data-foundation-design.md`, section 5 ("First PR: lineups only, and fieldability with ceilings"), plus section 6 for the metrics it starts recording (M1).

## Global Constraints

- Branch from the spec branch so the spec and this plan travel with the code: `git switch -c marcobraun2013/pr-a-lineup-only-fieldability marcobraun2013/data-foundation-spec` (that branch is `origin/main` plus the two docs commits). Never commit to `main`.
- Line length 100 (black + ruff via pre-commit). Do not run `black` on whole files by hand; stage only the lines you changed and let the hook format them. If the hook reformats unrelated lines in a file, revert those hunks before committing.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0176ehmDxCaLcMsBHwd5p5C8
  ```
- Run tests with `uv run pytest -q`. The whole suite must stay green after every task.
- `select_best_eleven` keeps returning a *partial* list for squads that cannot field eleven. Replay, backtest and `scoring/decision.py` rely on that; the spec's "or raises" is implemented as *the lineup step refuses to submit*, not as an exception.
- The legal formation set is the conservative eight implied by the ceilings the bot has used all season (DEF ≤ 5, MID ≤ 5, FW ≤ 3). `3-6-1` and `4-2-4` are **not** included until confirmed in the Kickbase app's formation picker (Task 6, step 2). A formation in the set that Kickbase rejects means a lineup that is never set.
- The spec's replay rule applies: Task 2 changes a decision path, so the season replay is measured before and after and the numbers go in the PR body.

______________________________________________________________________

### Task 1: Legal formations and `fieldability_from_counts`

**Files:**

- Modify: `rehoboam/formation.py` (constants at lines 18–29, `can_fill_starting_eleven` at lines 172–219)
- Test: `tests/test_formation_legal.py` (new)

**Interfaces:**

- Consumes: `FormationRequirements`, `get_position_counts` (existing).

- Produces:

  - `LEGAL_FORMATIONS: frozenset[tuple[int, int, int]]` — `(defenders, midfielders, forwards)`, one goalkeeper implicit.
  - `_POSITION_MAX_STARTERS` — now derived from `LEGAL_FORMATIONS` (same values as today: GK 1, DEF 5, MID 5, FW 3).
  - `@dataclass(frozen=True) class Fieldability(ok: bool, reason: str, counts: dict[str, int], purchases: int, positions: frozenset[str])`.
  - `fieldability_from_counts(counts: dict[str, int]) -> Fieldability`.
  - `fieldability(available: list) -> Fieldability`.
  - `is_legal_formation(players: list) -> bool`.
  - `can_fill_starting_eleven(available: list) -> dict` — unchanged signature, now a thin wrapper.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_formation_legal.py`:

```python
"""Fieldability that knows Kickbase's legal formations (spec 2026-09-11 §5).

On 2026-09-11 the squad was 1 GK, 6 DEF, 3 MID, 1 FW. `can_fill_starting_eleven`
checked minimums only (GK 1, DEF 3, MID 2, FW 1) and said "fieldable". Only five
defenders can start in any formation, so the most that squad can field is ten,
and Kickbase answered every lineup call since 2026-09-09 with
`LineupNotEnoughPlayers`. The question is not "are there eleven bodies" but
"does some legal formation fit", and if not, "what is the cheapest way to make
one fit".
"""

from __future__ import annotations

from rehoboam.formation import (
    LEGAL_FORMATIONS,
    _POSITION_MAX_STARTERS,
    can_fill_starting_eleven,
    fieldability,
    fieldability_from_counts,
    is_legal_formation,
)
from rehoboam.kickbase_client import Player


def _p(pid: str, position: str) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=50.0,
    )


def _squad(gk: int, de: int, mi: int, fw: int) -> list[Player]:
    return (
        [_p(f"g{i}", "Goalkeeper") for i in range(gk)]
        + [_p(f"d{i}", "Defender") for i in range(de)]
        + [_p(f"m{i}", "Midfielder") for i in range(mi)]
        + [_p(f"f{i}", "Forward") for i in range(fw)]
    )


class TestLegalFormations:
    def test_every_formation_has_ten_outfield_players(self):
        for d, m, f in LEGAL_FORMATIONS:
            assert d + m + f == 10, (d, m, f)

    def test_the_conservative_eight(self):
        assert LEGAL_FORMATIONS == frozenset(
            {
                (3, 4, 3),
                (3, 5, 2),
                (4, 3, 3),
                (4, 4, 2),
                (4, 5, 1),
                (5, 2, 3),
                (5, 3, 2),
                (5, 4, 1),
            }
        )

    def test_ceilings_are_derived_from_the_set(self):
        assert _POSITION_MAX_STARTERS == {
            "Goalkeeper": 1,
            "Defender": 5,
            "Midfielder": 5,
            "Forward": 3,
        }


class TestFieldabilityFromCounts:
    def test_the_2026_09_11_squad_is_one_short_at_mid_or_fw(self):
        fb = fieldability_from_counts(
            {"Goalkeeper": 1, "Defender": 6, "Midfielder": 3, "Forward": 1}
        )
        assert fb.ok is False
        assert fb.purchases == 1
        assert fb.positions == frozenset({"Midfielder", "Forward"})
        assert "Defender 6 > 5" in fb.reason
        assert "10 of 11" in fb.reason

    def test_a_legal_eleven_needs_nothing(self):
        fb = fieldability_from_counts(
            {"Goalkeeper": 1, "Defender": 4, "Midfielder": 4, "Forward": 2}
        )
        assert fb.ok is True
        assert fb.purchases == 0
        assert fb.positions == frozenset()

    def test_seven_players_need_four(self):
        """The real 2026-08-31 shape: GK 1, DEF 4, MID 2, FW 0."""
        fb = fieldability_from_counts(
            {"Goalkeeper": 1, "Defender": 4, "Midfielder": 2, "Forward": 0}
        )
        assert fb.purchases == 4
        assert "Forward" in fb.positions and "Midfielder" in fb.positions

    def test_no_goalkeeper_is_one_purchase_at_goalkeeper(self):
        fb = fieldability_from_counts(
            {"Goalkeeper": 0, "Defender": 6, "Midfielder": 5, "Forward": 3}
        )
        assert fb.purchases == 1
        assert fb.positions == frozenset({"Goalkeeper"})
        assert "Goalkeeper: have 0, need 1" == fb.reason

    def test_empty_squad_needs_eleven(self):
        fb = fieldability_from_counts({})
        assert fb.purchases == 11

    def test_two_short_lists_every_position_some_minimal_plan_uses(self):
        """GK 1, DEF 6, MID 3, FW 0 (10 players): 5-3-2 needs two forwards,
        5-4-1 needs a midfielder and a forward. Both plans cost two, so both
        positions are acceptable buys."""
        fb = fieldability_from_counts(
            {"Goalkeeper": 1, "Defender": 6, "Midfielder": 3, "Forward": 0}
        )
        assert fb.purchases == 2
        assert fb.positions == frozenset({"Midfielder", "Forward"})


class TestWrappers:
    def test_can_fill_starting_eleven_keeps_its_dict_shape(self):
        result = can_fill_starting_eleven(_squad(1, 6, 3, 1))
        assert result["ok"] is False
        assert result["counts"]["Defender"] == 6
        assert "Defender 6 > 5" in result["reason"]

    def test_fieldability_counts_from_players(self):
        assert fieldability(_squad(1, 4, 4, 2)).ok is True

    def test_is_legal_formation_accepts_a_4_4_2(self):
        assert is_legal_formation(_squad(1, 4, 4, 2)) is True

    def test_is_legal_formation_rejects_ten_players(self):
        assert is_legal_formation(_squad(1, 5, 3, 1)) is False

    def test_is_legal_formation_rejects_5_5_1(self):
        assert is_legal_formation(_squad(1, 5, 5, 1)) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_formation_legal.py -q`
Expected: FAIL with `ImportError: cannot import name 'LEGAL_FORMATIONS'`.

- [ ] **Step 3: Implement**

In `rehoboam/formation.py`, replace the block from the `# Maximum players per position` comment through the closing `}` of `_POSITION_MAX_STARTERS` (lines 18–29) with:

```python
#: Formations Kickbase accepts, as (defenders, midfielders, forwards); one
#: goalkeeper is implicit. This is the conservative set implied by the ceilings
#: the bot has submitted lineups with all season (DEF 5, MID 5, FW 3). The app
#: may also offer 3-6-1 and 4-2-4: add them ONLY after confirming them in the
#: app's formation picker. A formation listed here that Kickbase rejects means a
#: lineup that is never set, which is worse than a formation we never use.
LEGAL_FORMATIONS: frozenset[tuple[int, int, int]] = frozenset(
    {
        (3, 4, 3),
        (3, 5, 2),
        (4, 3, 3),
        (4, 4, 2),
        (4, 5, 1),
        (5, 2, 3),
        (5, 3, 2),
        (5, 4, 1),
    }
)

# Maximum players per position that can ever start across the legal
# formations. Derived, so the two can never disagree. Used to detect "dead
# weight" — a player who can never enter any starting 11 because the position
# is already saturated.
_POSITION_MAX_STARTERS = {
    "Goalkeeper": 1,
    "Defender": max(d for d, _, _ in LEGAL_FORMATIONS),
    "Midfielder": max(m for _, m, _ in LEGAL_FORMATIONS),
    "Forward": max(f for _, _, f in LEGAL_FORMATIONS),
}

_POSITION_ORDER = ("Goalkeeper", "Defender", "Midfielder", "Forward")


@dataclass(frozen=True)
class Fieldability:
    """Can these players field a legal eleven, and if not, what would it take?

    ``purchases`` is the smallest number of players to buy so that some legal
    formation fits. ``positions`` is the union of positions across every plan
    of that minimal size — any one of them is an acceptable next buy. ``ok``
    is ``purchases == 0``. ``reason`` is human-readable and stable enough for
    logs: "Only N available players, need 11", "<Position>: have X, need Y",
    or "Only N of M can start in any formation (<Position> X > ceiling)".
    """

    ok: bool
    reason: str
    counts: dict[str, int]
    purchases: int
    positions: frozenset[str]


def _shortfall(
    counts: dict[str, int], formation: tuple[int, int, int]
) -> dict[str, int]:
    d, m, f = formation
    need = {"Goalkeeper": 1, "Defender": d, "Midfielder": m, "Forward": f}
    return {pos: max(0, n - counts.get(pos, 0)) for pos, n in need.items()}


def fieldability_from_counts(counts: dict[str, int]) -> Fieldability:
    """Answer fieldability from position counts alone.

    Pure on counts so callers can ask "and after buying a midfielder?" by
    adding one to a copy — the emergency basket does exactly that.
    """
    requirements = FormationRequirements()
    plans = [_shortfall(counts, formation) for formation in LEGAL_FORMATIONS]
    purchases = min(sum(plan.values()) for plan in plans)
    positions = frozenset(
        pos
        for plan in plans
        if sum(plan.values()) == purchases
        for pos, n in plan.items()
        if n > 0
    )
    available = sum(counts.get(pos, 0) for pos in _POSITION_ORDER)

    if purchases == 0:
        reason = "Legal starting 11 available"
    elif available < requirements.starting_eleven_size:
        reason = f"Only {available} available players, need {requirements.starting_eleven_size}"
    else:
        minimums = {
            "Goalkeeper": requirements.min_goalkeepers,
            "Defender": requirements.min_defenders,
            "Midfielder": requirements.min_midfielders,
            "Forward": requirements.min_forwards,
        }
        unmet = next(
            (pos for pos in _POSITION_ORDER if counts.get(pos, 0) < minimums[pos]), None
        )
        if unmet is not None:
            reason = f"{unmet}: have {counts.get(unmet, 0)}, need {minimums[unmet]}"
        else:
            over = ", ".join(
                f"{pos} {counts.get(pos, 0)} > {ceiling}"
                for pos, ceiling in _POSITION_MAX_STARTERS.items()
                if counts.get(pos, 0) > ceiling
            )
            fieldable = sum(
                min(counts.get(pos, 0), ceiling)
                for pos, ceiling in _POSITION_MAX_STARTERS.items()
            )
            reason = (
                f"Only {fieldable} of {available} can start in any formation ({over}); "
                f"need {', '.join(sorted(positions))}"
            )

    return Fieldability(
        ok=purchases == 0,
        reason=reason,
        counts={pos: counts.get(pos, 0) for pos in _POSITION_ORDER},
        purchases=purchases,
        positions=positions,
    )


def fieldability(available: list) -> Fieldability:
    """`fieldability_from_counts` over a list of players (uses ``.position``)."""
    return fieldability_from_counts(get_position_counts(available))


def is_legal_formation(players: list) -> bool:
    """True when ``players`` is exactly eleven in a formation Kickbase accepts."""
    if len(players) != FormationRequirements().starting_eleven_size:
        return False
    counts = get_position_counts(players)
    if counts["Goalkeeper"] != 1:
        return False
    return (
        counts["Defender"],
        counts["Midfielder"],
        counts["Forward"],
    ) in LEGAL_FORMATIONS
```

`get_position_counts` is defined *after* this block in the file today. Python resolves the name at call time, so the order is fine, but move nothing.

Then replace the body of `can_fill_starting_eleven` (keep its docstring) with:

```python
    fb = fieldability(available)
    return {"ok": fb.ok, "reason": fb.reason, "counts": fb.counts}
```

- [ ] **Step 4: Run the new tests and the existing fieldability tests**

Run: `uv run pytest tests/test_formation_legal.py tests/test_squad_floor_guardrail.py tests/test_emergency_fieldability_trigger.py tests/test_emergency_trigger.py tests/test_scoring/test_dead_weight.py -q`
Expected: all PASS. The old tests assert on substrings ("Goalkeeper", "Defender", "10" or "11") that the new reasons keep.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add rehoboam/formation.py tests/test_formation_legal.py
git commit -m "feat(formation): legal formations as data; fieldability says how many to buy and where

can_fill_starting_eleven enforced minimums only, so 1 GK / 6 DEF / 3 MID /
1 FW passed as fieldable and the lineup went out with ten names every
session since 2026-09-09. Fieldability now asks whether some legal
formation fits and, if not, the cheapest way to make one fit."
```

______________________________________________________________________

### Task 2: Formation-aware `select_best_eleven`

**Files:**

- Modify: `rehoboam/formation.py` (`select_best_eleven`, lines 90–147 before Task 1's insertion shifted them)
- Test: `tests/test_formation_legal.py` (extend)

**Interfaces:**

- Consumes: `LEGAL_FORMATIONS` (Task 1).

- Produces: `select_best_eleven(squad, player_values) -> list` — same signature. Returns the highest-scoring **legal** eleven when one exists; otherwise the old greedy partial list (unchanged behaviour for unfieldable squads).

- [ ] **Step 1: Record the replay baseline before touching the selector**

Run: `uv run rehoboam replay-season 2>&1 | tail -15`
Copy the final total-points line into a scratch note. This is the number the after-change run is compared to in step 6. (If the command needs paths, both defaults exist: `logs/training_corpus.db` and `logs/bid_learning.db`.)

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_formation_legal.py`:

```python
from rehoboam.formation import select_best_eleven


class TestSelectBestElevenIsFormationAware:
    def test_never_returns_an_illegal_eleven_when_a_legal_one_exists(self):
        """GK 1, DEF 5, MID 5, FW 1 with flat scores: the old greedy filled
        5-5-1, which Kickbase does not accept."""
        squad = _squad(1, 5, 5, 1)
        values = {p.id: 50.0 for p in squad}
        eleven = select_best_eleven(squad, values)
        assert len(eleven) == 11
        assert is_legal_formation(eleven)

    def test_picks_the_highest_scoring_legal_formation(self):
        """Three strong forwards should pull the eleven toward 3-4-3 / 4-3-3,
        not be capped by whatever the greedy pass filled first."""
        squad = _squad(1, 5, 5, 3)
        values = {p.id: 40.0 for p in squad}
        for pid in ("f0", "f1", "f2"):
            values[pid] = 90.0
        eleven = select_best_eleven(squad, values)
        assert is_legal_formation(eleven)
        assert {p.id for p in eleven} >= {"f0", "f1", "f2"}
        total = sum(values[p.id] for p in eleven)
        assert total == 3 * 90.0 + 8 * 40.0

    def test_falls_back_to_a_partial_list_when_nothing_fits(self):
        """The 2026-09-11 squad. Callers in replay and decision code rely on
        a partial result here; refusing to submit it is the lineup step's job."""
        squad = _squad(1, 6, 3, 1)
        values = {p.id: 50.0 for p in squad}
        eleven = select_best_eleven(squad, values)
        assert len(eleven) == 10
        assert sum(1 for p in eleven if p.position == "Defender") == 5

    def test_ties_are_deterministic(self):
        squad = _squad(1, 5, 5, 3)
        values = {p.id: 10.0 for p in squad}
        first = [p.id for p in select_best_eleven(squad, values)]
        second = [p.id for p in select_best_eleven(squad, values)]
        assert first == second
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_formation_legal.py::TestSelectBestElevenIsFormationAware -q`
Expected: the first two FAIL (`is_legal_formation(eleven)` is False for 5-5-1; the forwards test gets fewer than three forwards).

- [ ] **Step 4: Implement**

In `rehoboam/formation.py`, rename the existing function to `_greedy_partial_eleven` (keep its body and docstring, add one line to the docstring: "Fallback for squads that cannot field a legal eleven; returns fewer than eleven."). Then add, directly above it:

```python
def select_best_eleven(squad: list, player_values: dict[str, float]) -> list:
    """Select the best starting eleven that Kickbase will accept.

    Tries every legal formation: the top goalkeeper plus the top ``d`` / ``m``
    / ``f`` players at each position by ``player_values``, and keeps the
    formation with the highest total. Exact, and only eight formations wide.

    When no legal formation fits (too few bodies, or a position over its
    ceiling with nothing to fill the rest), falls back to the greedy partial
    list the bot has always produced, so callers that reason about a
    short squad — replay, backtest, marginal-EP — keep their behaviour. The
    lineup step checks ``is_legal_formation`` before submitting.
    """
    by_position: dict[str, list] = {pos: [] for pos in _POSITION_ORDER}
    ranked = sorted(squad, key=lambda p: player_values.get(p.id, 0), reverse=True)
    for player in ranked:
        if player.position in by_position:
            by_position[player.position].append(player)

    best: list | None = None
    best_total = float("-inf")
    for d, m, f in sorted(LEGAL_FORMATIONS):
        need = {"Goalkeeper": 1, "Defender": d, "Midfielder": m, "Forward": f}
        if any(len(by_position[pos]) < n for pos, n in need.items()):
            continue
        eleven = [p for pos in _POSITION_ORDER for p in by_position[pos][: need[pos]]]
        total = sum(player_values.get(p.id, 0) for p in eleven)
        if total > best_total:
            best, best_total = eleven, total
    if best is not None:
        return best
    return _greedy_partial_eleven(squad, player_values)
```

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. Tests that exercise the selector on legal squads (`tests/test_scoring/test_dead_weight.py::TestSelectBestElevenPositionCap`, `tests/test_scoring/test_trade_pairs.py`, `tests/test_replay/test_fieldability_guard.py`, `tests/test_fallback_ep_recency.py`) must stay green. If one fails, read its assertion: it is either pinning the greedy's *order* (then compare as sets) or pinning an illegal formation (then the test was wrong; fix the expectation and say so in the commit).

- [ ] **Step 6: Replay gate**

Run: `uv run rehoboam replay-season 2>&1 | tail -15`
Compare the final total to step 1. A legal, exact best-eleven should score at least what the greedy did. Record both numbers; they go into the PR body. If the total is *lower* by more than 1%, stop and report: the selector or a test fixture is wrong, and shipping is not the fix.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/formation.py tests/test_formation_legal.py
git commit -m "feat(formation): select_best_eleven picks the best LEGAL eleven

The greedy pass could return 5-5-1 or 4-4-3, which Kickbase rejects. Try
every legal formation and keep the highest total; keep the greedy partial
list only for squads that cannot field eleven. Replay: <before> -> <after>."
```

Replace `<before>` and `<after>` with the two numbers from steps 1 and 6.

______________________________________________________________________

### Task 3: Gap-aware emergency basket

**Files:**

- Modify: `rehoboam/services/emergency_basket.py` (`_value`, `_rank_key`, `_best_exact`, `_best_greedy`, `select_emergency_basket`)
- Test: `tests/test_emergency_basket_gap.py` (new)

**Interfaces:**

- Consumes: nothing new.

- Produces: `select_emergency_basket(candidates, slots_short, budget, gap_after=None) -> list[EmergencyPick]`. `gap_after: Callable[[Sequence[str]], int] | None` — given the positions of a basket, returns how many purchases the squad would still need. With `gap_after=None` behaviour is byte-for-byte today's (the existing `tests/test_emergency_basket.py` must not change).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_emergency_basket_gap.py`:

```python
"""The basket is scored by the slots it actually closes (spec 2026-09-11 §5).

On 2026-09-07 the squad was 1 GK, 6 DEF, 3 MID, 1 FW: ten can start, one slot
short, and only a midfielder or forward can close it. The basket objective
counted bodies — `+100 x players_bought` — so with EUR 2,334,394 to spend it
bought Stergiou, a seventh defender, for 2,294,821 and the lineup stayed at
ten. With `gap_after` the objective counts closed slots, and a player who
closes none is not an emergency buy at all.
"""

from __future__ import annotations

from rehoboam.formation import fieldability_from_counts
from rehoboam.services.emergency_basket import (
    EmergencyCandidate,
    select_emergency_basket,
)

SQUAD_2026_09_11 = {"Goalkeeper": 1, "Defender": 6, "Midfielder": 3, "Forward": 1}


def _gap_after_for(counts: dict[str, int]):
    def gap_after(positions):
        after = dict(counts)
        for pos in positions:
            after[pos] = after.get(pos, 0) + 1
        return fieldability_from_counts(after).purchases

    return gap_after


GUTIERREZ = EmergencyCandidate(
    "3759",
    "Gutiérrez",
    ask=22_308_405,
    max_bid=28_418_405,
    ep=63.5,
    position="Defender",
)
STERGIOU = EmergencyCandidate(
    "7257", "Stergiou", ask=2_054_821, max_bid=2_294_821, ep=3.9, position="Defender"
)
REGEER = EmergencyCandidate(
    "17288", "Regeer", ask=6_239_298, max_bid=8_079_298, ep=68.4, position="Midfielder"
)
MOFFI = EmergencyCandidate(
    "17203", "Moffi", ask=4_398_390, max_bid=5_497_987, ep=62.4, position="Forward"
)
BOARD = [GUTIERREZ, STERGIOU, REGEER, MOFFI]


class TestOneShortWithSaturatedDefence:
    def test_buys_the_midfielder_that_closes_the_slot(self):
        picks = select_emergency_basket(
            BOARD,
            slots_short=1,
            budget=12_929_567,
            gap_after=_gap_after_for(SQUAD_2026_09_11),
        )
        assert [p.candidate.id for p in picks] == [REGEER.id]

    def test_a_seventh_defender_is_not_an_emergency_buy(self):
        """The 2026-09-07 session: only Stergiou is affordable. He closes
        nothing, so the answer is 'nothing', not 'Stergiou'."""
        picks = select_emergency_basket(
            BOARD,
            slots_short=1,
            budget=2_334_394,
            gap_after=_gap_after_for(SQUAD_2026_09_11),
        )
        assert picks == []

    def test_a_cheaper_forward_wins_when_the_midfielder_is_unaffordable(self):
        picks = select_emergency_basket(
            BOARD,
            slots_short=1,
            budget=5_000_000,
            gap_after=_gap_after_for(SQUAD_2026_09_11),
        )
        assert [p.candidate.id for p in picks] == [MOFFI.id]


class TestTwoShort:
    """GK 1, DEF 6, MID 3, FW 0: two purchases. MID+MID leaves 5-4-1 a forward
    short; MID+FW or FW+FW closes both. Counting bodies would pick the two
    midfielders (EP 70 + 65) and leave a slot empty."""

    COUNTS = {"Goalkeeper": 1, "Defender": 6, "Midfielder": 3, "Forward": 0}
    M1 = EmergencyCandidate(
        "m1", "M1", ask=1_000_000, max_bid=1_100_000, ep=70.0, position="Midfielder"
    )
    M2 = EmergencyCandidate(
        "m2", "M2", ask=1_000_000, max_bid=1_100_000, ep=65.0, position="Midfielder"
    )
    F1 = EmergencyCandidate(
        "f1", "F1", ask=1_000_000, max_bid=1_100_000, ep=40.0, position="Forward"
    )
    F2 = EmergencyCandidate(
        "f2", "F2", ask=1_000_000, max_bid=1_100_000, ep=35.0, position="Forward"
    )

    def test_closes_both_slots_rather_than_maximising_ep(self):
        picks = select_emergency_basket(
            [self.M1, self.M2, self.F1, self.F2],
            slots_short=2,
            budget=10_000_000,
            gap_after=_gap_after_for(self.COUNTS),
        )
        assert {p.candidate.id for p in picks} == {"m1", "f1"}

    def test_with_money_for_one_it_still_closes_one(self):
        picks = select_emergency_basket(
            [self.M1, self.M2, self.F1, self.F2],
            slots_short=2,
            budget=1_500_000,
            gap_after=_gap_after_for(self.COUNTS),
        )
        assert [p.candidate.id for p in picks] == ["m1"]


class TestGreedyPathAlsoRespectsTheGap:
    def test_a_large_pool_never_picks_a_saturated_position(self):
        pool = [
            EmergencyCandidate(
                f"d{i}",
                f"D{i}",
                ask=1_000_000,
                max_bid=1_000_000,
                ep=90.0,
                position="Defender",
            )
            for i in range(20)
        ] + [
            EmergencyCandidate(
                "m",
                "M",
                ask=1_000_000,
                max_bid=1_000_000,
                ep=20.0,
                position="Midfielder",
            )
        ]
        picks = select_emergency_basket(
            pool,
            slots_short=1,
            budget=50_000_000,
            gap_after=_gap_after_for(SQUAD_2026_09_11),
        )
        assert [p.candidate.id for p in picks] == ["m"]


def test_without_gap_after_the_old_objective_is_untouched():
    """`tests/test_emergency_basket.py` pins that behaviour in full; this is
    the one-line reminder that the default path did not move."""
    picks = select_emergency_basket(BOARD, slots_short=1, budget=12_929_567)
    assert len(picks) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_emergency_basket_gap.py -q`
Expected: FAIL with `TypeError: select_emergency_basket() got an unexpected keyword argument 'gap_after'`.

- [ ] **Step 3: Implement**

In `rehoboam/services/emergency_basket.py`:

Add to the imports:

```python
from collections.abc import Callable, Sequence
```

and below `_EXACT_ENUMERATION_LIMIT`:

```python
#: Given the positions a basket would add, how many purchases the squad would
#: still need. `None` means every player counts as a slot (the pre-2026-09-11
#: objective), kept for callers that have not computed fieldability.
GapAfter = Callable[[Sequence[str]], int]
```

Replace `_rank_key` with:

```python
def _rank_key(
    members: tuple[EmergencyCandidate, ...],
    slots_short: int = 0,
    gap_after: GapAfter | None = None,
) -> tuple:
    """Sort key for picking the best basket. Higher is better.

    With ``gap_after`` the slots a basket actually closes come first — an
    emergency fill exists to make an eleven fieldable, and a 150-EP player at
    a saturated position does not — then expected points, then lower cost.
    Without it, the original ``(value, -cost)``.
    """
    cost = -sum(c.ask for c in members)
    if gap_after is None:
        return (_value(members), cost)
    closed = slots_short - gap_after([c.position for c in members])
    return (closed, sum(c.ep for c in members), cost)
```

Replace `_best_exact` with:

```python
def _best_exact(
    candidates: list[EmergencyCandidate],
    slots_short: int,
    budget: int,
    gap_after: GapAfter | None = None,
) -> tuple[EmergencyCandidate, ...]:
    best: tuple[EmergencyCandidate, ...] = ()
    best_key: tuple | None = None
    for size in range(1, min(slots_short, len(candidates)) + 1):
        for combo in combinations(candidates, size):
            if sum(c.ask for c in combo) > budget:
                continue
            key = _rank_key(combo, slots_short, gap_after)
            if best_key is None or key > best_key:
                best, best_key = combo, key
    if gap_after is not None and best and best_key is not None and best_key[0] <= 0:
        return ()  # closes nothing: not an emergency buy
    return best
```

Replace `_best_greedy` with:

```python
def _best_greedy(
    candidates: list[EmergencyCandidate],
    slots_short: int,
    budget: int,
    gap_after: GapAfter | None = None,
) -> tuple[EmergencyCandidate, ...]:
    """Feasibility-preserving greedy, for a pool too large to enumerate.

    Without ``gap_after``: takes candidates in value order but refuses any
    that would leave too little to afford the cheapest remaining fillers —
    the check that keeps cardinality intact.

    With ``gap_after``: one pick at a time, choosing the affordable candidate
    that closes the most slots, then the most expected points, then the
    cheapest; stops as soon as no candidate closes anything.
    """
    if gap_after is not None:
        chosen: list[EmergencyCandidate] = []
        remaining = budget
        gap = slots_short
        while len(chosen) < slots_short and gap > 0:
            pool = [c for c in candidates if c not in chosen and 0 < c.ask <= remaining]
            if not pool:
                break

            def gain(c: EmergencyCandidate) -> tuple:
                after = gap_after([x.position for x in chosen] + [c.position])
                return (gap - after, c.ep, -c.ask)

            pick = max(pool, key=gain)
            closed = gain(pick)[0]
            if closed <= 0:
                break
            chosen.append(pick)
            remaining -= pick.ask
            gap -= closed
        return tuple(chosen)

    order = sorted(candidates, key=lambda c: (-_priority(c), c.ask))
    by_price = sorted(candidates, key=lambda c: c.ask)

    target = 0
    running = 0
    for c in by_price:
        if target >= slots_short:
            break
        if running + c.ask > budget:
            break
        running += c.ask
        target += 1

    chosen = []
    remaining = budget
    for c in order:
        if len(chosen) >= target:
            break
        still_needed = target - len(chosen) - 1
        pool = [x for x in by_price if x.id != c.id and x not in chosen]
        cheapest = sum(x.ask for x in pool[:still_needed])
        if c.ask + cheapest <= remaining:
            chosen.append(c)
            remaining -= c.ask
    return tuple(chosen)
```

(The second half is the existing body, unchanged.)

Replace the signature and body of `select_emergency_basket` with:

```python
def select_emergency_basket(
    candidates: list[EmergencyCandidate],
    slots_short: int,
    budget: int,
    gap_after: GapAfter | None = None,
) -> list[EmergencyPick]:
    """Choose the basket of buys that scores the most points this matchday.

    Args:
        candidates: Buyable players, already filtered for wash trades and
            existing bids by the caller.
        slots_short: How many players an eleven is missing.
        budget: Money available to commit, in euros.
        gap_after: Optional. Given the positions of a basket, how many
            purchases the squad would still need. When provided, a basket
            is ranked by the slots it closes before anything else, and a
            basket that closes none is rejected — a seventh defender does
            not fix a lineup that already has six.

    Returns:
        The chosen players with the bid to place for each, ask <= bid <=
        max_bid, totalling no more than `budget`. Empty when nothing is
        affordable, or nothing affordable closes a slot.
    """
    if slots_short <= 0 or budget <= 0:
        return []

    affordable = [c for c in candidates if 0 < c.ask <= budget]
    if not affordable:
        return []

    if len(affordable) <= _EXACT_ENUMERATION_LIMIT:
        members = _best_exact(affordable, slots_short, budget, gap_after)
    else:
        members = _best_greedy(affordable, slots_short, budget, gap_after)

    if not members:
        return []
    return _spend_leftover(members, budget)
```

- [ ] **Step 4: Run both basket test files**

Run: `uv run pytest tests/test_emergency_basket_gap.py tests/test_emergency_basket.py -q`
Expected: PASS. `tests/test_emergency_basket.py` is unchanged and must stay green: it proves the `gap_after=None` path did not move.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/services/emergency_basket.py tests/test_emergency_basket_gap.py
git commit -m "feat(emergency): score the basket by the slots it closes, not the bodies it adds

With gap_after the objective is (slots closed, EP, cost). A player at a
saturated position closes nothing and is refused; on 2026-09-07 the old
objective bought a seventh defender for 2,294,821 and the lineup stayed
at ten."
```

______________________________________________________________________

### Task 4: Wire fieldability into the session: slots short, fill positions, and a lineup that is never illegal

**Files:**

- Modify: `rehoboam/auto_trader.py` — `_emergency_slots_short` (line ~285), `_run_emergency_squad_fill` (line ~1432: the `gap_positions` block and the `select_emergency_basket` call and the reserves list), `_set_optimal_lineup` (line ~2363: after `best_eleven = select_best_eleven(...)`)
- Test: `tests/test_lineup_legality.py` (new)

**Interfaces:**

- Consumes: `fieldability`, `fieldability_from_counts`, `get_position_counts`, `is_legal_formation`, `get_formation_string` (formation.py); `select_emergency_basket(..., gap_after=)` (Task 3).

- Produces: no new public names. `_run_emergency_squad_fill(league, ctx, fresh_squad, slots_short)` keeps its signature (an existing test asserts on `call_args.args[3]`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lineup_legality.py`:

```python
"""The session never submits an illegal lineup and fills the right position.

Two call sites, tested at the call site (the REH-103 precedent): a writer that
exists and is never called is how a season of NULLs happens.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from rehoboam.auto_trader import (
    AutoTrader,
    EPSessionContext,
    MatchdayPhase,
    _emergency_slots_short,
)
from rehoboam.config import Settings
from rehoboam.kickbase_client import Player

LEAGUE = SimpleNamespace(id="1933872", name="PUMARUDEL")


def _player(pid: str, position: str, price: int = 1_000_000) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=price,
        points=0,
        average_points=50.0,
    )


def _squad(gk: int, de: int, mi: int, fw: int) -> list[Player]:
    return (
        [_player(f"g{i}", "Goalkeeper") for i in range(gk)]
        + [_player(f"d{i}", "Defender") for i in range(de)]
        + [_player(f"m{i}", "Midfielder") for i in range(mi)]
        + [_player(f"f{i}", "Forward") for i in range(fw)]
    )


class _Api:
    user = SimpleNamespace(id="3616202")

    def __init__(self, squad):
        self._squad = squad
        self.lineups: list[tuple[str, list[str]]] = []

    def get_squad(self, league):
        return list(self._squad)

    def get_my_bids(self, league):
        return []

    def get_team_info(self, league):
        return {"budget": 12_929_567, "team_value": 144_177_545}

    def set_lineup(self, league, formation, player_ids):
        self.lineups.append((formation, list(player_ids)))
        return {}


def _scores(squad):
    return [SimpleNamespace(player_id=p.id, expected_points=50.0) for p in squad]


def _trader(api, monkeypatch, tmp_path, dry_run=False) -> AutoTrader:
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)
    return AutoTrader(api=api, settings=Settings(), dry_run=dry_run)


class TestSlotsShortUsesLegalFormations:
    def test_six_defenders_is_one_short(self):
        assert _emergency_slots_short(_squad(1, 6, 3, 1)) == 1

    def test_a_legal_squad_is_not_short(self):
        assert _emergency_slots_short(_squad(1, 4, 4, 2)) == 0


class TestTheLineupStepRefusesTenNames:
    def test_illegal_eleven_is_not_submitted_and_is_an_error(
        self, monkeypatch, tmp_path
    ):
        api = _Api(_squad(1, 6, 3, 1))
        trader = _trader(api, monkeypatch, tmp_path)
        errors: list[str] = []

        lineup = trader._set_optimal_lineup(
            LEAGUE, errors, squad_scores=_scores(api._squad)
        )

        assert lineup == []
        assert api.lineups == [], "ten names must never reach Kickbase"
        assert any("lineup not legal" in e for e in errors)

    def test_legal_eleven_is_submitted_with_a_legal_formation(
        self, monkeypatch, tmp_path
    ):
        api = _Api(_squad(1, 5, 5, 3))
        trader = _trader(api, monkeypatch, tmp_path)
        errors: list[str] = []

        lineup = trader._set_optimal_lineup(
            LEAGUE, errors, squad_scores=_scores(api._squad)
        )

        assert len(lineup) == 11
        assert errors == []
        ((formation, ids),) = api.lineups
        assert len(ids) == 11
        d, m, f = (int(x) for x in formation.split("-"))
        assert (d, m, f) in {
            (3, 4, 3),
            (3, 5, 2),
            (4, 3, 3),
            (4, 4, 2),
            (4, 5, 1),
            (5, 2, 3),
            (5, 3, 2),
            (5, 4, 1),
        }


class TestTheFillTargetsTheOpenPosition:
    def _ctx(self, squad, buy_recs) -> EPSessionContext:
        return EPSessionContext(
            ep_result={"buy_recs": buy_recs, "squad_scores": [], "market_players": {}},
            matchday_phase=MatchdayPhase(
                days_until_match=4,
                phase="moderate",
                max_trades=2,
                allow_flips=False,
                reason="t",
            ),
            my_bids=[],
            my_bid_amounts={},
            squad=list(squad),
            current_budget=12_929_567,
            team_value=144_177_545,
            flip_budget=0,
        )

    def _rec(self, pid: str, position: str, ep: float, price: int):
        # `_run_emergency_squad_fill` reads `rec.player.price`, which only a
        # MarketPlayer carries; a namespace with the fields it touches is enough.
        return SimpleNamespace(
            player=SimpleNamespace(
                id=pid,
                first_name="F",
                last_name=f"P{pid}",
                position=position,
                price=price,
            ),
            recommended_bid=price,
            marginal_ep_gain=ep,
        )

    def test_gap_after_and_fills_gap_describe_the_real_squad(
        self, monkeypatch, tmp_path
    ):
        squad = _squad(1, 6, 3, 1)
        recs = [
            self._rec("3759", "Defender", 63.5, 22_308_405),
            self._rec("17288", "Midfielder", 68.4, 6_239_298),
            self._rec("17203", "Forward", 62.4, 4_398_390),
        ]
        api = _Api(squad)
        trader = _trader(api, monkeypatch, tmp_path, dry_run=True)
        ctx = self._ctx(squad, recs)

        with (
            patch(
                "rehoboam.services.emergency_basket.select_emergency_basket",
                return_value=[],
            ) as basket,
            patch.object(AutoTrader, "_is_wash_trade", return_value=False),
        ):
            trader._run_emergency_squad_fill(LEAGUE, ctx, squad, slots_short=1)

        assert basket.called
        candidates = basket.call_args.args[0]
        gap_after = basket.call_args.kwargs["gap_after"]
        assert {c.id: c.fills_gap for c in candidates} == {
            "3759": False,
            "17288": True,
            "17203": True,
        }
        assert gap_after(["Midfielder"]) == 0
        assert gap_after(["Forward"]) == 0
        assert gap_after(["Defender"]) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_lineup_legality.py -q`
Expected: `TestTheLineupStepRefusesTenNames::test_illegal_eleven_is_not_submitted_and_is_an_error` FAILS (a 10-name `5-3-1` reaches the fake API); `TestTheFillTargetsTheOpenPosition` FAILS with `KeyError: 'gap_after'`; the slots-short tests may already pass after Task 1.

- [ ] **Step 3: Implement `_emergency_slots_short`**

Replace the function body (keep the first paragraph of the docstring, replace the rest):

```python
def _emergency_slots_short(squad: list) -> int:
    """How many players must be bought to make a legal eleven fieldable.

    Zero when the squad can already field one. Answered by
    :func:`rehoboam.formation.fieldability`, which knows the legal formations:
    eleven bodies with six defenders can field ten, and this returns 1 for
    them, at a position the fill path reads from the same answer.

    The squad is passed unfiltered. Excluding injured and suspended players
    would be better still, but it makes emergencies fire more often in the
    locked phase and is a behaviour change worth measuring on its own.
    """
    from .formation import fieldability

    return fieldability(squad).purchases
```

- [ ] **Step 4: Implement the fill's position awareness**

In `_run_emergency_squad_fill`, replace the block that computes `position_counts` and `gap_positions` (from `from .config import POSITION_MINIMUMS` through the closing `}` of `gap_positions = {...}`) with:

```python
        from .formation import fieldability_from_counts, get_position_counts

        counts = get_position_counts(fresh_squad)
        need = fieldability_from_counts(counts)
        gap_positions = set(need.positions)

        def _gap_after(positions) -> int:
            after = dict(counts)
            for pos in positions:
                after[pos] = after.get(pos, 0) + 1
            return fieldability_from_counts(after).purchases
```

Change the basket call to:

```python
picks = select_emergency_basket(
    candidates, slots_short, budget_remaining, gap_after=_gap_after
)
```

And restrict the reserves behind the basket to positions that help. Replace:

```python
attempts += [
    (by_id[c.id], c.max_bid)
    for c in sorted(candidates, key=lambda c: -c.ep)
    if c.id not in chosen_ids
]
```

with:

```python
attempts += [
    (by_id[c.id], c.max_bid)
    for c in sorted(candidates, key=lambda c: -c.ep)
    if c.id not in chosen_ids and c.position in gap_positions
]
```

`fills_gap=rec.player.position in gap_positions` a few lines above already picks up the new meaning of `gap_positions`; leave it.

- [ ] **Step 5: Implement the lineup refusal**

In `_set_optimal_lineup`, change the import line to:

```python
from .formation import (
    get_formation_string,
    get_position_counts,
    is_legal_formation,
    order_for_lineup,
    select_best_eleven,
)
```

and directly after `best_eleven = select_best_eleven(squad, ep_scores)` insert:

```python
if not is_legal_formation(best_eleven):
    counts = get_position_counts(squad)
    msg = (
        f"lineup not legal: {len(best_eleven)} players as "
        f"{get_formation_string(best_eleven)} — squad "
        f"GK {counts['Goalkeeper']} DEF {counts['Defender']} "
        f"MID {counts['Midfielder']} FW {counts['Forward']}; not submitted"
    )
    console.print(f"[red]{msg}[/red]")
    logger.error("lineup-illegal %s", msg)
    errors.append(msg)
    return []
```

- [ ] **Step 6: Run the new tests and the emergency suite**

Run: `uv run pytest tests/test_lineup_legality.py tests/test_emergency_fill_every_phase.py tests/test_emergency_fieldability_trigger.py tests/test_emergency_fill_approval.py tests/test_min_hold_and_emergency_fill.py tests/test_pacing_emergency_and_fail_open.py -q`
Expected: PASS.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add rehoboam/auto_trader.py tests/test_lineup_legality.py
git commit -m "fix(auto): the fill buys the position the formation needs; the lineup step never submits ten

slots_short comes from fieldability over the legal formations, the basket
gets a gap_after callback built from the same counts, reserves are limited
to positions that help, and _set_optimal_lineup refuses an illegal eleven
with a logged error instead of a 500 from Kickbase."
```

______________________________________________________________________

### Task 5: `trading_mode` and the lineups-only session

**Files:**

- Modify: `rehoboam/config.py` (imports; new field next to `dry_run`, line ~528)
- Modify: `rehoboam/auto_trader.py` (`run_full_session`: session-start log, step 1 deferred sells, the locked branch, the session-end log; new method `_finish_lineup_only`)
- Test: `tests/test_trading_mode.py` (new)

**Interfaces:**

- Consumes: nothing new.

- Produces: `Settings.trading_mode: Literal["full", "lineup_only"]` (env `TRADING_MODE`); `AutoTrader._finish_lineup_only(league, ctx, trade_results, errors, start_time, reason) -> AutoTradeSession`; log lines `session-start ... mode=<mode>` and `session-end ... mode=<mode> ...` on **every** exit path.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trading_mode.py`:

```python
"""`trading_mode=lineup_only`: lineups and the emergency fill, nothing else.

Decided 2026-09-11 (spec 2026-09-11 §5): while the data foundation is rebuilt
the bot must not sell or buy, except to make an eleven fieldable. Tested at
the call sites, because a mode that skips the wrong step is indistinguishable
from a quiet market.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from rehoboam.auto_trader import AutoTrader, EPSessionContext, MatchdayPhase
from rehoboam.config import Settings
from rehoboam.kickbase_client import Player

LEAGUE = SimpleNamespace(id="1933872", name="PUMARUDEL")


def _player(pid: str, position: str) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=50.0,
    )


def _legal_squad() -> list[Player]:
    return (
        [_player("gk", "Goalkeeper")]
        + [_player(f"d{i}", "Defender") for i in range(4)]
        + [_player(f"m{i}", "Midfielder") for i in range(4)]
        + [_player(f"f{i}", "Forward") for i in range(2)]
    )


def _short_squad() -> list[Player]:
    return (
        [_player("gk", "Goalkeeper")]
        + [_player(f"d{i}", "Defender") for i in range(6)]
        + [_player(f"m{i}", "Midfielder") for i in range(3)]
        + [_player("f0", "Forward")]
    )


class _Api:
    user = SimpleNamespace(id="3616202")

    def __init__(self, squad):
        self._squad = squad

    def get_squad(self, league):
        return list(self._squad)

    def get_my_bids(self, league):
        return []

    def get_team_info(self, league):
        return {"budget": 5_868_658, "team_value": 149_641_186}


def _context(squad, phase="moderate", days=4) -> EPSessionContext:
    return EPSessionContext(
        ep_result={"squad_scores": [], "market_players": {}},
        matchday_phase=MatchdayPhase(
            days_until_match=days,
            phase=phase,
            max_trades=10,
            allow_flips=False,
            reason="t",
        ),
        my_bids=[],
        my_bid_amounts={},
        squad=list(squad),
        current_budget=5_868_658,
        team_value=149_641_186,
        flip_budget=0,
    )


def _run(squad, mode, tmp_path, monkeypatch, phase="moderate", days=4):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.chdir(tmp_path)
    trader = AutoTrader(api=_Api(squad), settings=Settings(), dry_run=True)
    ctx = _context(squad, phase=phase, days=days)
    with (
        patch.object(AutoTrader, "_build_session_context", return_value=ctx),
        patch.object(AutoTrader, "_run_emergency_squad_fill", return_value=[]) as fill,
        patch.object(AutoTrader, "run_profit_sell_phase", return_value=[]) as sells,
        patch.object(
            AutoTrader, "optimize_and_execute_squad", return_value=[]
        ) as optimise,
        patch.object(AutoTrader, "run_unified_trade_phase", return_value=[]) as trades,
        patch.object(AutoTrader, "_evaluate_open_bids", return_value=None) as bid_eval,
        patch.object(AutoTrader, "_set_optimal_lineup", return_value=[]) as lineup,
    ):
        session = trader.run_full_session(LEAGUE)
    return SimpleNamespace(
        session=session,
        fill=fill,
        sells=sells,
        optimise=optimise,
        trades=trades,
        bid_eval=bid_eval,
        lineup=lineup,
    )


class TestSettings:
    def test_default_is_full(self, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.delenv("TRADING_MODE", raising=False)
        assert Settings().trading_mode == "full"

    def test_lineup_only_parses_from_env(self, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.setenv("TRADING_MODE", "lineup_only")
        assert Settings().trading_mode == "lineup_only"

    def test_anything_else_is_rejected(self, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.setenv("TRADING_MODE", "observe")
        with pytest.raises(ValidationError):
            Settings()


class TestLineupOnlySkipsEveryTradingStep:
    def test_no_sell_or_buy_phase_runs(self, tmp_path, monkeypatch):
        r = _run(_legal_squad(), "lineup_only", tmp_path, monkeypatch)
        assert not r.sells.called
        assert not r.optimise.called
        assert not r.trades.called
        assert not r.bid_eval.called
        assert r.lineup.call_count == 1

    def test_the_emergency_fill_still_runs(self, tmp_path, monkeypatch):
        r = _run(_short_squad(), "lineup_only", tmp_path, monkeypatch)
        assert r.fill.called
        assert r.fill.call_args.args[3] == 1
        assert r.lineup.call_count == 1

    def test_locked_phase_still_exits_after_the_lineup(self, tmp_path, monkeypatch):
        r = _run(
            _legal_squad(), "lineup_only", tmp_path, monkeypatch, phase="locked", days=1
        )
        assert not r.trades.called
        assert r.lineup.call_count == 1


class TestFullModeIsUnchanged:
    def test_trading_steps_run(self, tmp_path, monkeypatch):
        r = _run(_legal_squad(), "full", tmp_path, monkeypatch)
        assert r.sells.called
        assert r.optimise.called
        assert r.trades.called
        assert r.lineup.call_count == 1


class TestTheModeIsInTheLogs:
    def test_session_start_and_end_carry_the_mode(self, tmp_path, monkeypatch, caplog):
        with caplog.at_level(logging.INFO, logger="rehoboam.auto_trader"):
            _run(_legal_squad(), "lineup_only", tmp_path, monkeypatch)
        starts = [m for m in caplog.messages if m.startswith("session-start")]
        ends = [m for m in caplog.messages if m.startswith("session-end")]
        assert starts and "mode=lineup_only" in starts[0]
        assert ends and "mode=lineup_only" in ends[0]

    def test_a_locked_session_logs_session_end_too(self, tmp_path, monkeypatch, caplog):
        """Today the locked branch returns without a session-end line, which
        is why App Insights shows none for 2026-09-10 20:00 and 2026-09-11 08:00."""
        with caplog.at_level(logging.INFO, logger="rehoboam.auto_trader"):
            _run(_legal_squad(), "full", tmp_path, monkeypatch, phase="locked", days=1)
        assert any(
            m.startswith("session-end") and "mode=full" in m for m in caplog.messages
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_trading_mode.py -q`
Expected: `TestSettings::test_anything_else_is_rejected` FAILS (no such field, nothing rejects it), `TestLineupOnlySkipsEveryTradingStep` FAILS (`sells.called` is True), `TestTheModeIsInTheLogs` FAILS (no `mode=` in the lines).

- [ ] **Step 3: Add the setting**

In `rehoboam/config.py`, add `from typing import Literal` to the imports, and directly after the `dry_run` field add:

```python
trading_mode: Literal["full", "lineup_only"] = Field(
    default="full",
    description=(
        "What a session may do. 'full' trades. 'lineup_only' sets the lineup, "
        "runs the emergency fill, records learning, and skips every sell and "
        "buy phase — the mode prod runs while the data foundation is rebuilt "
        "(spec 2026-09-11 §5). Env: TRADING_MODE."
    ),
)
```

- [ ] **Step 4: Gate the session**

In `rehoboam/auto_trader.py`, `run_full_session`:

(a) The session-start log. Replace:

```python
logger.info(
    "session-start league=%s dry_run=%s max_trades=%d max_spend=%d",
    getattr(league, "name", league.id),
    self.dry_run,
    self.max_trades_per_session,
    self.max_daily_spend,
)
```

with:

```python
        logger.info(
            "session-start league=%s mode=%s dry_run=%s max_trades=%d max_spend=%d",
            getattr(league, "name", league.id),
            self.settings.trading_mode,
            self.dry_run,
            self.max_trades_per_session,
            self.max_daily_spend,
        )
        if self.settings.trading_mode == "lineup_only":
            console.print("[yellow]MODE lineup_only — no sells, no buys except the emergency fill[/yellow]")
```

(b) Step 1 deferred sell plans. Replace `if deferred_sell_ids:` with:

```python
            if deferred_sell_ids and self.settings.trading_mode != "full":
                console.print(
                    f"[yellow]Mode lineup_only — {len(deferred_sell_ids)} deferred sell "
                    f"plan(s) skipped[/yellow]"
                )
                logger.info(
                    "trading-mode lineup_only: deferred sell plans skipped n=%d",
                    len(deferred_sell_ids),
                )
            elif deferred_sell_ids:
```

(c) The locked branch. Replace the whole block from `# If locked (match imminent), set the lineup and exit` through its `return AutoTradeSession(...)` (the block ending just before `# Step 4: Trend-aware profit selling`) with:

```python
        # Two reasons to stop after the lineup: the match is imminent, or the
        # bot is in lineup_only mode (spec 2026-09-11 §5). The emergency fill
        # above has already had its chance to make an eleven fieldable.
        stop_reason: str | None = None
        if ctx.matchday_phase.phase == "locked":
            stop_reason = f"Match imminent ({ctx.matchday_phase.days_until_match}d)"
        elif self.settings.trading_mode == "lineup_only":
            stop_reason = "Mode lineup_only"
        if stop_reason is not None:
            return self._finish_lineup_only(
                league, ctx, trade_results, errors, start_time, stop_reason
            )
```

(d) Add the method directly after `run_full_session` (before `_set_optimal_lineup`):

```python
def _finish_lineup_only(
    self,
    league,
    ctx: EPSessionContext,
    trade_results: list[AutoTradeResult],
    errors: list[str],
    start_time: float,
    reason: str,
) -> AutoTradeSession:
    """Set the lineup and end the session without trading.

    Shared by the locked phase and by ``trading_mode=lineup_only`` so the
    two exits cannot drift apart, and so both log ``session-end`` — the
    locked branch used to return without one, which is why prod telemetry
    shows no session-end for 2026-09-10 20:00 or 2026-09-11 08:00.
    """
    console.print(f"[yellow]{reason} — setting lineup only, no trading[/yellow]")
    self._send_proposal_overview(league, ctx)
    lineup = (
        self._set_optimal_lineup(
            league, errors, squad_scores=ctx.ep_result.get("squad_scores")
        )
        or []
    )
    total_spent = sum(r.price for r in trade_results if r.action == "BUY" and r.success)
    total_earned = sum(
        r.price for r in trade_results if r.action == "SELL" and r.success
    )
    end_time = time.time()
    logger.info(
        "session-end duration=%.1fs mode=%s phase=%s sells=0 trades=%d/%d "
        "spent=%d earned=%d net=%d errors=%d | %s",
        end_time - start_time,
        self.settings.trading_mode,
        ctx.matchday_phase.phase,
        len([r for r in trade_results if r.success]),
        len(trade_results),
        total_spent,
        total_earned,
        total_earned - total_spent,
        len(errors),
        reason,
    )
    return AutoTradeSession(
        start_time=start_time,
        end_time=end_time,
        profit_trades=trade_results,
        lineup_trades=[],
        errors=errors,
        total_spent=total_spent,
        total_earned=total_earned,
        net_change=total_earned - total_spent,
        lineup=lineup,
    )
```

(e) The normal-path session-end log near the end of `run_full_session`. Replace:

```python
        logger.info(
            "session-end duration=%.1fs phase=%s sells=%d trades=%d/%d "
            "spent=%d earned=%d net=%d errors=%d",
            end_time - start_time,
            ctx.matchday_phase.phase,
```

with:

```python
        logger.info(
            "session-end duration=%.1fs mode=%s phase=%s sells=%d trades=%d/%d "
            "spent=%d earned=%d net=%d errors=%d",
            end_time - start_time,
            self.settings.trading_mode,
            ctx.matchday_phase.phase,
```

(f) The step-2 fallback path (EP pipeline failed) returns before any `session-end`; leave it — it already logs an exception, and it is out of scope.

- [ ] **Step 5: Run the new tests and the session tests**

Run: `uv run pytest tests/test_trading_mode.py tests/test_emergency_fill_every_phase.py tests/test_auto_trader_flip_switches.py tests/test_pacing_session.py -q`
Expected: PASS. `test_emergency_fill_every_phase` drives the locked branch through the new helper and must not change.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/config.py rehoboam/auto_trader.py tests/test_trading_mode.py
git commit -m "feat(auto): trading_mode=lineup_only — lineups and the emergency fill, nothing else

One exit helper for the locked phase and the new mode, so both log
session-end (the locked branch never did). Deferred sell plans are skipped
and logged in lineup_only; steps 4-7 do not run."
```

______________________________________________________________________

### Task 6: Deploy plumbing, docs, live smoke, PR

**Files:**

- Modify: `deploy/bicep/main.bicep` (params near line 73; app settings map near line 296)
- Modify: `deploy/deploy.sh` (`deploy_params` near line 60; guards near line 80)
- Modify: `.env.example` (line 44 area; line 106 area)
- Modify: `CLAUDE.md` (Configuration list near line 219; "Current state" list near line 37)

**Interfaces:**

- Produces: Bicep parameter `tradingMode`, app setting `TRADING_MODE` on the trading app; `.env` variable `DEPLOY_TRADING_MODE` read by `deploy.sh`.

- [ ] **Step 1: Bicep**

In `deploy/bicep/main.bicep`, after the `aggressiveMode` parameter add:

```bicep
@description('What a trading session may do: full | lineup_only. Read by Settings.trading_mode.')
param tradingMode string = 'full'
```

and in the app-settings `union({ ... })` map, after `AGGRESSIVE: aggressiveMode` add:

```bicep
    TRADING_MODE: tradingMode
```

- [ ] **Step 2: Verify the legal formation set against the app**

Open the Kickbase app, lineup screen, formation picker. Write down every formation offered. If it offers exactly the eight in `LEGAL_FORMATIONS`, nothing changes. If it also offers `3-6-1` and/or `4-2-4`: add the tuple(s) to `LEGAL_FORMATIONS` in `rehoboam/formation.py`, update `test_the_conservative_eight` and `test_ceilings_are_derived_from_the_set` in `tests/test_formation_legal.py` to the new values (MID ceiling becomes 6 with 3-6-1; FW ceiling 4 with 4-2-4), update the four pinned values in `tests/test_scoring/test_dead_weight.py` lines 115–124 to match, and re-run `uv run pytest -q`. Record what the app showed in the PR body either way. Do not add a formation you did not see.

- [ ] **Step 3: deploy.sh**

In `deploy_params()` add a line after `"aggressiveMode=${DEPLOY_AGGRESSIVE}"`:

```bash
       "tradingMode=${DEPLOY_TRADING_MODE}"
```

(add a trailing backslash to the `aggressiveMode` line). After the two `: "${DEPLOY_...:?...}"` guards add:

```bash
  : "${DEPLOY_TRADING_MODE:?must be set in .env — an infra deploy overwrites the live value}"
```

and change the echo to:

```bash
  echo "==> Deploying with DRY_RUN=$DEPLOY_DRY_RUN AGGRESSIVE=$DEPLOY_AGGRESSIVE TRADING_MODE=$DEPLOY_TRADING_MODE"
```

- [ ] **Step 4: .env.example**

After the `DRY_RUN=true` line add:

```
TRADING_MODE=full  # full | lineup_only — lineup_only sets lineups and runs the emergency fill only
```

After `DEPLOY_AGGRESSIVE=true` add:

```
DEPLOY_TRADING_MODE=lineup_only
```

- [ ] **Step 5: CLAUDE.md**

In the Configuration list, after the `DRY_RUN` line add:

```
- `TRADING_MODE`: `full` or `lineup_only` (default `full`). In `lineup_only` a session sets the lineup, runs the emergency fill and records learning, and skips every sell and buy phase. Prod runs `lineup_only` while the data foundation (spec `docs/superpowers/specs/2026-09-11-data-foundation-design.md`) is rebuilt.
```

In the "Current state" bullet list, add as the first bullet:

```
- **Lineups only (2026-09-11)**: prod runs `TRADING_MODE=lineup_only` until the data foundation's calibration gate passes (spec §4). Fieldability knows Kickbase's legal formations (`formation.LEGAL_FORMATIONS`); `select_best_eleven` returns the best legal eleven; the emergency fill buys the position a formation needs; `_set_optimal_lineup` never submits an illegal eleven.
```

- [ ] **Step 6: Live smoke against prod, read-only**

Run: `uv run rehoboam -v status 2>&1 | grep -E "session-start|MODE|lineup not legal|Formation:|lineup emergency|emergency-basket|session-end"`
Expected, with the squad as of 2026-09-11 (if it is still 1/6/3/1): `lineup emergency: ... slots_short=1`, `emergency-basket ... picked=1` naming a midfielder or forward (or `No affordable ... candidates` if none fits the budget), and `lineup not legal: 10 players as 5-3-1 ...` instead of a ten-name `Formation:` line. If Marco has since fixed the squad by hand, expect a `Formation:` line with eleven names in a legal formation. Paste the lines into the PR body.

- [ ] **Step 7: Commit and open the PR**

```bash
git add deploy/bicep/main.bicep deploy/deploy.sh .env.example CLAUDE.md rehoboam/formation.py tests/
git commit -m "chore(deploy): TRADING_MODE flows through Bicep and deploy.sh; docs

An infra deploy replaces the whole app-settings collection, so the mode
lives in Bicep with a required DEPLOY_TRADING_MODE, like DRY_RUN."
git push -u origin marcobraun2013/pr-a-lineup-only-fieldability
gh pr create --title "feat: lineups only, and fieldability that knows the legal formations (PR A)" --body-file - <<'PRBODY'
## What

PR A of `docs/superpowers/specs/2026-09-11-data-foundation-design.md` (§5).

- `TRADING_MODE=lineup_only`: lineup + emergency fill + learning; no sells, no buys.
- `formation.LEGAL_FORMATIONS` as data; `fieldability_from_counts` answers "how many to buy, where".
- `select_best_eleven` returns the best **legal** eleven (greedy partial only when none fits).
- Emergency basket scores baskets by the slots they close (`gap_after`); a seventh defender is refused.
- `_set_optimal_lineup` never submits an illegal eleven.
- Locked-phase sessions now log `session-end`.

## Evidence

- Replay: <before> -> <after> (Task 2 step 6).
- Formation picker in the app showed: <list> (Task 6 step 2).
- Live `status` against prod: <pasted lines> (Task 6 step 6).
- `uv run pytest -q`: <N passed>.

## Deploy

1. `az functionapp config appsettings set -n func-rehoboam -g rg-rehoboam --settings TRADING_MODE=lineup_only`
2. `bash deploy/deploy.sh code trading`
3. Verify in App Insights: `session-start ... mode=lineup_only`, `session-end ... mode=lineup_only`.
4. Set `DEPLOY_TRADING_MODE=lineup_only` in `.env` so the next `deploy.sh infra` keeps it.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_0176ehmDxCaLcMsBHwd5p5C8
PRBODY
```

Fill every `<...>` in the body with the measured value before running the command.

______________________________________________________________________

## Self-review against the spec (§5 and §6)

| spec requirement                                                              | task            |
| ----------------------------------------------------------------------------- | --------------- |
| `Settings.trading_mode`, env `TRADING_MODE`, default `full`                   | 5               |
| Step 1 reconciles bids only; deferred sell plans skipped and logged           | 5 (b)           |
| Steps 2, 2a, 3 run; locked branch runs; steps 4–7 skipped; step 8 runs        | 5 (c), (d)      |
| Board/log says the mode; `session-end` carries `mode=`                        | 5 (a), (d), (e) |
| Fieldability uses ceilings and a legal-formation check; list kept as data     | 1               |
| Emergency basket buys the missing position, never a seventh defender          | 3, 4            |
| `select_best_eleven` returns a legal eleven; lineup step never submits ten    | 2, 4            |
| Legal formation list verified against the app before shipping                 | 6 step 2        |
| Replay before shipping a decision change                                      | 2 steps 1 and 6 |
| Live `--dry-run` smoke before merge                                           | 6 step 6        |
| `TRADING_MODE` survives an infra deploy                                       | 6 steps 1, 3, 4 |
| M1 becomes observable (no illegal lineups; errors logged as `lineup-illegal`) | 4 step 5        |

Deviation, stated: the spec says `select_best_eleven` "returns a legal eleven or raises". Raising would break replay, backtest and marginal-EP callers that rely on a partial result for short squads; the refusal lives in `_set_optimal_lineup` instead (Task 4), which is the only caller that talks to Kickbase.
