# REH-55: Integrate the v2 Scorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the fitted v2 scorer the bot's live scoring path in real Kickbase points, re-derive every threshold whose meaning changes with the scale, and delete the legacy modules that carry the saturation defect.

**Architecture:** A thin adapter composes the fitted availability and rate models into the existing `PlayerScore` contract, so `trader.py`'s two call sites change one function name. Thresholds are then re-derived from the *new* marginal-gain distribution measured through the real `DecisionEngine`, not scaled from the old ones. Legacy deletion lands last, once nothing falls back to it.

**Tech Stack:** Python 3.12, `uv`, pytest. Standard library only.

## Global Constraints

- **No new runtime dependencies.** Standard library only; this ships to an Azure Function.
- **Do not refit the models.** `rehoboam/scoring/v2/coefficients.json` must be byte-identical at the end of this plan. Verify with `git diff --quiet`.
- **No serving-time `P(status)` overrides in this plan.** Live lineup probability and injury status stay unused. See "The coupling hazard" below — this is a deliberate decision, not an oversight.
- **Do NOT run `black` on whole files.** The repo is not black-clean. Ruff enforces `B905`.
- **Never commit to `main`.** Branch: `feat/reh-55-integrate-v2-scorer`.
- Output is in **real Kickbase points**, never a 0–100 index.

## The coupling hazard — read before touching availability

`rate.predict()` is **not** a calibrated within-status estimate. Quality is normalised against a pooled 3+5 mean (72.66) while base rates are per-status (91.5 / 18.3), so quality absorbs *start-share* as well as *skill*: `predict(5)` overshoots pure starters by **+24%**, `predict(3)` undershoots pure substitutes by **−52%**, and `corr(quality, start_share) = 0.681`.

The **composed** model is calibrated — mean predicted 58.47 vs actual 57.71 (+1.3%), MAE 43.79 — and a textbook within-status renormalisation is *worse* (MAE 44.95). The availability model carries no player effect, so quality absorbing start-share acts as a crude player-level correction. **The two components were fitted as a pair.**

Consequence: the moment anything sets `P(started) → 1.0` for a confirmed starter, that coupling breaks and the raw +24% bias is exposed with nothing cancelling it. Nothing fails; every starter is simply scored 24% too high. **This plan therefore uses the model exactly as fitted.** Overrides are a separate ticket that must renormalise quality within-status first.

## Why thresholds must be re-derived, not scaled

|                  | old scale                | new scale                            |
| ---------------- | ------------------------ | ------------------------------------ |
| EP range         | 25 – 112 (span **87.1**) | 2.3 – 199.0 (span **196.7**)         |
| EP median        | ~72                      | 34.8                                 |
| `must_have` tier | `marginal_ep_gain >= 20` | **same constant, different meaning** |

The old distribution was narrow *because of the saturation defect* — 93.1% of players shared an identical base. Scaling from it bakes in the compression this work removes.

A replacement-level proxy over the 2025/26 squad gives marginal gains of **p50 = 28.0, p70 = 37.9, p85 = 49.8** above replacement (median EP among starters = 46.6). Against those, the current `must_have >= 20` would fire on **more than half** of all upgrade candidates — routine upgrades classified as must-haves, `tier_bonus = 10.0` applied liberally, aggressive bidding on marginally better players.

That proxy is not the answer; it establishes the size of the problem. Task 2 measures the real distribution through `DecisionEngine.calculate_marginal_ep`.

**Note:** production data cannot supply the old firing rates — `predicted_eps.marginal_ep_gain` is **NULL on all 351 rows**, a third learning field never populated alongside `transfer_pnl` and `winning_overbid_pct`. Worth its own ticket; out of scope here.

______________________________________________________________________

## File Structure

**Created:**

| File                                                          | Responsibility                                                                                 |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `rehoboam/scoring/v2/adapter.py`                              | `score_player_v2(data) -> PlayerScore` — composes the fitted models into the existing contract |
| `rehoboam/scoring/v2/thresholds.py`                           | Measures the marginal-gain distribution and reports derived thresholds                         |
| `tests/test_scoring_v2/test_adapter.py`, `test_thresholds.py` |                                                                                                |

**Modified:** `rehoboam/trader.py` (two call sites), `rehoboam/config.py` (thresholds), `rehoboam/bidding_strategy.py` (bid tiers), `rehoboam/auto_trader.py` (legacy fallback removal), `rehoboam/cli.py` (a `derive-thresholds` command).

**Deleted (Task 4):** `rehoboam/expected_points.py`, `rehoboam/value_calculator.py`, `tests/test_expected_points.py`.

______________________________________________________________________

## Task 1: The v2 adapter

**Files:**

- Create: `rehoboam/scoring/v2/adapter.py`, `tests/test_scoring_v2/test_adapter.py`

**Interfaces:**

- Consumes: `load_coefficients()` (`coefficients.py`), `AvailabilityModel.predict`, `RateModel.predict`, `PlayerData`/`PlayerScore` (`scoring/models.py`), `parse_minutes` (`match_parsing.py`)
- Produces:
  - `score_player_v2(data: PlayerData) -> PlayerScore`
  - `last_played_status(performance: dict | None) -> int | None`
  - `compose_ep(player_id, prev_status, position, availability, rate) -> float`

### Design decisions this task locks in

**`PlayerScore` keeps its shape.** It carries v1's decomposition (`base_points`, `consistency_bonus`, `lineup_bonus`, `fixture_bonus`, `form_bonus`, `minutes_bonus`) which has no v2 counterpart. Changing the dataclass would ripple into `decision.py`, `trader.py`, `learning/tracker.py` and their tests for no behavioural gain. **Set the v1-only fields to 0.0** and record the real decomposition in `notes`. `expected_points` is the only field any decision reads.

**Availability input is the player's last played status**, read from the same `performance` payload the v1 scorer already receives — no new API calls. When there is no prior match (a new signing, or an empty history), pass `None`, which the model handles by falling back to its marginal prior.

**No `calibration_multiplier`.** `score_player` accepts one from REH-20's position calibration, fitted against the old scale to correct what was actually a unit mismatch. Applying it to real points would reintroduce a correction for a defect that no longer exists. `score_player_v2` takes no such parameter.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.scoring.v2.adapter — composing fitted models into PlayerScore."""

from __future__ import annotations

import pytest

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.models import PlayerData
from rehoboam.scoring.v2.adapter import (
    compose_ep,
    last_played_status,
    score_player_v2,
)
from rehoboam.scoring.v2.availability import fit_availability
from rehoboam.scoring.v2.features import FeatureRow
from rehoboam.scoring.v2.rate import fit_rate


def _perf(matches: list[dict]) -> dict:
    return {"it": [{"ti": "2025/2026", "ph": matches}]}


def _player(pid: str = "1") -> MarketPlayer:
    return MarketPlayer(
        id=pid,
        first_name="Test",
        last_name="Player",
        position="Midfielder",
        team_id="2",
        team_name="T",
        market_value=1_000_000,
        price=1_000_000,
        points=0,
        average_points=0.0,
    )


def _data(pid: str = "1", performance: dict | None = None) -> PlayerData:
    return PlayerData(
        player=_player(pid),
        performance=performance,
        player_details=None,
        team_strength=None,
        opponent_strength=None,
        is_dgw=False,
    )


def _row(pid: str, prev: int | None, status: int, points: int) -> FeatureRow:
    return FeatureRow(
        player_id=pid,
        season="2024/2025",
        day_number=1,
        prev_status=prev,
        rolling_minutes_3=90.0,
        matches_seen=5,
        target_status=status,
        target_points=points,
    )


def test_last_played_status_reads_the_most_recent_played_match():
    perf = _perf(
        [
            {"day": 1, "st": 5, "p": 80, "mp": "90'"},
            {"day": 2, "st": 3, "p": 12, "mp": "20'"},
        ]
    )
    assert last_played_status(perf) == 3


def test_last_played_status_ignores_unplayed_fixtures():
    """status 0 means the fixture has not happened — it is not 'his last state'."""
    perf = _perf(
        [
            {"day": 1, "st": 5, "p": 80, "mp": "90'"},
            {"day": 2, "st": 0},
        ]
    )
    assert last_played_status(perf) == 5


def test_last_played_status_returns_none_without_history():
    assert last_played_status(None) is None
    assert last_played_status({"it": []}) is None
    assert last_played_status(_perf([])) is None


def test_compose_ep_is_the_probability_weighted_sum():
    rows = [_row("1", 5, 5, 90)] * 20
    av, rate = fit_availability(rows), fit_rate(rows, {"1": "Midfielder"})
    probs = av.predict(5)
    expected = sum(probs[s] * rate.predict("1", s, "Midfielder") for s in (1, 3, 4, 5))
    assert compose_ep("1", 5, "Midfielder", av, rate) == pytest.approx(expected)


def test_score_is_in_real_points_not_an_index():
    """A player who reliably starts and scores ~90 should score near 90, not 40."""
    perf = _perf([{"day": d, "st": 5, "p": 90, "mp": "90'"} for d in range(1, 11)])
    score = score_player_v2(_data(performance=perf))
    assert score.expected_points > 50.0, "real points, not a 0-100 index"


def test_v1_only_fields_are_zeroed_and_explained():
    """PlayerScore carries v1's decomposition; v2 has no counterpart for it."""
    perf = _perf([{"day": 1, "st": 5, "p": 80, "mp": "90'"}])
    score = score_player_v2(_data(performance=perf))
    assert score.base_points == 0.0
    assert score.consistency_bonus == 0.0
    assert score.lineup_bonus == 0.0
    assert score.fixture_bonus == 0.0
    assert score.form_bonus == 0.0
    assert score.minutes_bonus == 0.0
    assert any("availability" in n.lower() for n in score.notes)


def test_player_with_no_history_still_scores():
    """A new signing must not crash — the model falls back to its prior."""
    score = score_player_v2(_data(pid="unknown-player", performance=None))
    assert score.expected_points >= 0.0
    assert score.player_id == "unknown-player"


def test_score_carries_identity_fields_decisions_depend_on():
    perf = _perf([{"day": 1, "st": 5, "p": 80, "mp": "90'"}])
    score = score_player_v2(_data(performance=perf))
    assert score.player_id == "1"
    assert score.position == "Midfielder"
    assert score.market_value == 1_000_000


def test_dgw_multiplies_the_composed_score():
    perf = _perf([{"day": 1, "st": 5, "p": 80, "mp": "90'"}])
    single = score_player_v2(_data(performance=perf))
    dgw_data = _data(performance=perf)
    (
        object.__setattr__(dgw_data, "is_dgw", True)
        if hasattr(dgw_data, "__setattr__")
        else None
    )
    dgw_data.is_dgw = True
    doubled = score_player_v2(dgw_data)
    assert doubled.expected_points > single.expected_points
    assert doubled.is_dgw is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring_v2/test_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.scoring.v2.adapter'`

- [ ] **Step 3: Implement**

`rehoboam/scoring/v2/adapter.py`:

```python
"""Compose the fitted v2 models into the existing ``PlayerScore`` contract.

    EP = Σ_status P(status | previous status) × rate(player, status)

Deliberate choices, each with a reason:

**``PlayerScore`` keeps its shape.** It carries v1's decomposition
(``base_points``, ``consistency_bonus``, ``lineup_bonus``, ``fixture_bonus``,
``form_bonus``, ``minutes_bonus``) which has no v2 counterpart. Changing the
dataclass would ripple through ``decision.py``, ``trader.py`` and
``learning/tracker.py`` for no behavioural gain, so those fields are set to 0.0
and the real decomposition is recorded in ``notes``. ``expected_points`` is the
only field any decision actually reads.

**No calibration multiplier.** ``scoring.scorer.score_player`` accepts one from
REH-20's position calibration, fitted against the old 0-100 index to correct
what was in fact a unit mismatch. Applying it to real points would reintroduce
a correction for a defect that no longer exists.

**No serving-time overrides.** Live lineup probability and injury status are not
consulted. ``rate.predict`` is not a calibrated within-status estimate — quality
absorbs start-share as well as skill — and the composed model is only calibrated
because availability and rate were fitted as a coupled pair. Overriding
``P(status)`` breaks that coupling and exposes a ~24% starter bias. See REH-55's
ticket notes before adding overrides.
"""

from __future__ import annotations

from functools import lru_cache

from rehoboam.scoring.models import DataQuality, PlayerData, PlayerScore
from rehoboam.scoring.v2.availability import AvailabilityModel
from rehoboam.scoring.v2.coefficients import load_coefficients
from rehoboam.scoring.v2.features import PLAYED_STATUSES
from rehoboam.scoring.v2.rate import RateModel

DGW_MULTIPLIER = 1.8


@lru_cache(maxsize=1)
def _models() -> tuple[AvailabilityModel, RateModel, dict]:
    """Load fitted coefficients once per process."""
    return load_coefficients()


def last_played_status(performance: dict | None) -> int | None:
    """The player's status in his most recent *played* match.

    Unplayed fixtures (status 0 or absent) are skipped — they describe a match
    that has not happened, not a state the player was in. Returns None when
    there is no played history, which the availability model handles by falling
    back to its marginal prior.
    """
    if not performance:
        return None

    latest: tuple[str, int] | None = None
    latest_status: int | None = None
    for season in performance.get("it") or []:
        title = season.get("ti") or ""
        for match in season.get("ph") or []:
            status = match.get("st")
            day = match.get("day")
            if status not in PLAYED_STATUSES or day is None:
                continue
            key = (title, int(day))
            if latest is None or key > latest:
                latest, latest_status = key, int(status)
    return latest_status


def compose_ep(
    player_id: str,
    prev_status: int | None,
    position: str | None,
    availability: AvailabilityModel,
    rate: RateModel,
) -> float:
    """Probability-weighted expected points, in real Kickbase points."""
    probs = availability.predict(prev_status)
    return sum(probs[s] * rate.predict(player_id, s, position) for s in PLAYED_STATUSES)


def score_player_v2(data: PlayerData) -> PlayerScore:
    """Score a player with the fitted v2 models. Pure — no I/O beyond cached load."""
    availability, rate, _meta = _models()
    player = data.player
    position = player.position or None

    prev_status = last_played_status(data.performance)
    ep = compose_ep(player.id, prev_status, position, availability, rate)

    dgw_multiplier = DGW_MULTIPLIER if data.is_dgw else 1.0
    ep *= dgw_multiplier

    probs = availability.predict(prev_status)
    notes = [
        f"v2: availability P(start)={probs[5]:.0%} "
        f"(prev status {prev_status if prev_status is not None else 'unknown'}), "
        f"rate={rate.predict(player.id, 5, position):.0f} pts if started"
    ]
    if player.id not in rate.quality:
        notes.append("No fitted quality — using position prior (cold start)")
    if data.is_dgw:
        notes.append("DOUBLE GAMEWEEK ×1.8")

    return PlayerScore(
        player_id=player.id,
        expected_points=round(ep, 2),
        data_quality=DataQuality(
            grade="A" if player.id in rate.quality else "C",
            games_played=0,
            consistency=0.0,
            has_fixture_data=False,
            has_lineup_data=False,
            warnings=[],
        ),
        # v1 decomposition — no v2 counterpart; see module docstring.
        base_points=0.0,
        consistency_bonus=0.0,
        lineup_bonus=0.0,
        fixture_bonus=0.0,
        form_bonus=0.0,
        minutes_bonus=0.0,
        dgw_multiplier=dgw_multiplier,
        is_dgw=data.is_dgw,
        next_opponent=(
            data.upcoming_opponent_strengths[0].team_name
            if data.upcoming_opponent_strengths
            else None
        ),
        notes=notes,
        current_price=getattr(player, "price", player.market_value),
        market_value=player.market_value,
        average_points=player.average_points or 0.0,
        position=player.position or "",
        lineup_probability=None,
        minutes_trend=None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring_v2/test_adapter.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Sanity-check against real players**

The unit tests use synthetic fixtures. Confirm the adapter produces sane scores for players you can recognise:

```bash
uv run python -c "
from pathlib import Path
import sqlite3
from rehoboam.scoring.v2.coefficients import load_coefficients
from rehoboam.scoring.v2.adapter import compose_ep
from rehoboam.scoring.v2.dataset import load_match_rows, load_positions
from rehoboam.scoring.v2.features import build_feature_rows
av, rate, _ = load_coefficients()
db = Path('logs/training_corpus.db'); pos = load_positions(db)
names = {str(p): n for p, n in sqlite3.connect(db).execute('SELECT player_id, last_name FROM player_universe')}
out = []
for pid, ms in load_match_rows(db).items():
    rows = [r for r in build_feature_rows(ms) if r.season == '2025/2026']
    if not rows: continue
    out.append((compose_ep(pid, rows[-1].target_status, pos.get(pid), av, rate), names.get(pid,'?')))
out.sort(reverse=True)
print('TOP 8:', [(n, round(e)) for e, n in out[:8]])
print('BOTTOM 5:', [(n, round(e)) for e, n in out[-5:]])
"
```

Expect recognisable elite players at the top with scores well above 100, and fringe players near zero. **If the ordering looks wrong or everything clusters, stop and report** — the adapter is the only thing between the fitted models and every decision the bot makes.

- [ ] **Step 6: Run the full suite and commit**

```bash
uv run pytest -q
git add rehoboam/scoring/v2/adapter.py tests/test_scoring_v2/test_adapter.py
git commit -m "feat(scoring): v2 adapter composing availability x rate into PlayerScore"
```

______________________________________________________________________

## Task 2: Derive thresholds from the real marginal-gain distribution

**Files:**

- Create: `rehoboam/scoring/v2/thresholds.py`, `tests/test_scoring_v2/test_thresholds.py`
- Modify: `rehoboam/cli.py` (a `derive-thresholds` command)

**Interfaces:**

- Consumes: `score_player_v2` (Task 1), `DecisionEngine.calculate_marginal_ep` (`scoring/decision.py:55`)
- Produces:
  - `ThresholdReport` dataclass: `n_candidates, gains: list[float], percentiles: dict[str, float], proposed: dict[str, float]`
  - `derive_thresholds(squad_scores, candidate_scores, squad, *, engine) -> ThresholdReport`

### Why this task exists

The constants in `config.py` and `bidding_strategy.py` were calibrated against a 0–100 index. On real points they mean something else entirely, and the old firing rates cannot be recovered — `predicted_eps.marginal_ep_gain` is NULL on all 351 production rows.

So the thresholds are derived from **rarity**, which is the property they were always meant to encode: a `must_have` should be rare, a `solid_upgrade` common-ish.

**Use the real `DecisionEngine.calculate_marginal_ep`** (`decision.py:55`), which computes best-11 totals before and after and returns `max(0, new_total - current_total)`. Do not reimplement it — a second marginal-gain calculation would drift from the one the bot actually uses, which is exactly the class of bug this project keeps finding.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.scoring.v2.thresholds — rarity-based threshold derivation."""

from __future__ import annotations

import pytest

from rehoboam.scoring.v2.thresholds import ThresholdReport, percentile, proposed_tiers


def test_percentile_picks_the_right_element():
    values = [float(i) for i in range(1, 101)]  # 1..100
    assert percentile(values, 0.50) == pytest.approx(51.0, abs=1.0)
    assert percentile(values, 0.85) == pytest.approx(86.0, abs=1.0)


def test_percentile_handles_empty_and_single():
    assert percentile([], 0.5) == 0.0
    assert percentile([7.0], 0.9) == 7.0


def test_proposed_tiers_are_ordered_and_rare_to_common():
    gains = [float(i) for i in range(1, 201)]
    tiers = proposed_tiers(gains)
    assert tiers["must_have"] > tiers["strong_upgrade"] > tiers["solid_upgrade"]


def test_proposed_tiers_reflect_the_intended_rarities():
    """must_have = top 15%, strong = top 30%, solid = top 50% of positive gains."""
    gains = [float(i) for i in range(1, 101)]
    tiers = proposed_tiers(gains)
    assert tiers["solid_upgrade"] == pytest.approx(percentile(gains, 0.50), abs=1.0)
    assert tiers["strong_upgrade"] == pytest.approx(percentile(gains, 0.70), abs=1.0)
    assert tiers["must_have"] == pytest.approx(percentile(gains, 0.85), abs=1.0)


def test_no_positive_gains_yields_zeros_not_a_crash():
    tiers = proposed_tiers([])
    assert tiers == {"must_have": 0.0, "strong_upgrade": 0.0, "solid_upgrade": 0.0}


def test_report_carries_the_sample_size():
    report = ThresholdReport(
        n_candidates=3,
        gains=[1.0, 2.0, 3.0],
        percentiles={"p50": 2.0},
        proposed={"must_have": 3.0},
    )
    assert report.n_candidates == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring_v2/test_thresholds.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`rehoboam/scoring/v2/thresholds.py`:

```python
"""Derive decision thresholds from the v2 marginal-gain distribution.

The constants in ``config.py`` and ``bidding_strategy.py`` were calibrated
against a 0-100 index whose range was **87.1** points and whose median sat near
72. Real points span **196.7** with a median of 34.8, so the same numeric
constant now means something entirely different — ``marginal_ep_gain >= 20``
went from "rare must-have" to "more than half of all upgrade candidates".

The old firing rates cannot be recovered: ``predicted_eps.marginal_ep_gain`` is
NULL on all 351 production rows. So thresholds are derived from **rarity**,
which is the property the tiers were always meant to encode.

Rarity targets, expressed over candidates with a positive marginal gain:

    must_have       top 15%   — defend hard, worth an aggressive bid
    strong_upgrade  top 30%
    solid_upgrade   top 50%

Marginal gains must come from ``DecisionEngine.calculate_marginal_ep``, the same
function the bot uses live. A second implementation would drift.
"""

from __future__ import annotations

from dataclasses import dataclass

RARITY = {"must_have": 0.85, "strong_upgrade": 0.70, "solid_upgrade": 0.50}


@dataclass(frozen=True)
class ThresholdReport:
    """Measured marginal-gain distribution and the thresholds derived from it."""

    n_candidates: int
    gains: list[float]
    percentiles: dict[str, float]
    proposed: dict[str, float]


def percentile(values: list[float], p: float) -> float:
    """Value at percentile ``p`` (0-1). Returns 0.0 for an empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * p), len(ordered) - 1)]


def proposed_tiers(gains: list[float]) -> dict[str, float]:
    """Tier thresholds at the intended rarities."""
    if not gains:
        return dict.fromkeys(RARITY, 0.0)
    return {name: percentile(gains, p) for name, p in RARITY.items()}


def build_report(gains: list[float]) -> ThresholdReport:
    """Summarise a measured marginal-gain distribution."""
    positive = [g for g in gains if g > 0]
    return ThresholdReport(
        n_candidates=len(positive),
        gains=positive,
        percentiles={
            f"p{int(p * 100)}": percentile(positive, p)
            for p in (0.10, 0.25, 0.50, 0.70, 0.85, 0.95)
        },
        proposed=proposed_tiers(positive),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring_v2/test_thresholds.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Add the `derive-thresholds` CLI command**

Append to `rehoboam/cli.py`, following the conventions of `enrich-corpus` and `backtest-baseline` (Typer options, Rich table, lazy imports in the body):

```python
@app.command("derive-thresholds")
def derive_thresholds(
    league_index: int = typer.Option(0, "--league", help="League index"),
):
    """Measure the v2 marginal-gain distribution and propose decision thresholds.

    The constants in config.py and bidding_strategy.py were calibrated against
    the old 0-100 EP index. On real points they mean something different, and
    the old firing rates cannot be recovered (predicted_eps.marginal_ep_gain is
    NULL on every production row). This measures the real distribution against
    the current squad and market, and proposes thresholds by rarity.

    Read-only: reports numbers, changes nothing.
    """
    from .scoring.decision import DecisionEngine
    from .scoring.v2.thresholds import build_report

    api, settings, league = _login_and_get_league(league_index)
    trader = Trader(api, settings)
    result = trader.get_ep_recommendations_with_trends(league)

    # NOTE: `get_ep_recommendations_with_trends` returns a **dict**, not a
    # dataclass. Verified keys: buy_recs, trade_pairs, sell_recs, squad_scores,
    # lineup_map, budget, squad_size, squad_players, market_players,
    # competitor_player_ids.
    #
    # Do NOT read marginal gains off `buy_recs`: `recommend_buys` returns only
    # the top-N already filtered and ranked, so its gains sample the good tail
    # and would push every derived threshold upward. Thresholds must be measured
    # over ALL market candidates.
    squad_scores = result["squad_scores"]
    squad_players = result["squad_players"]  # {player_id: MarketPlayer}
    market_players = result["market_players"]  # {player_id: MarketPlayer}
    squad = list(squad_players.values())

    engine = DecisionEngine(settings=settings)

    gains: list[float] = []
    for pid, player in market_players.items():
        candidate_score = market_scores.get(pid)
        if candidate_score is None:
            continue
        mep = engine.calculate_marginal_ep(
            candidate_score=candidate_score,
            candidate_player=player,
            squad=squad,
            squad_scores=squad_scores,
        )
        gains.append(mep.marginal_ep_gain)

    report = build_report(gains)

    table = Table(
        title=f"v2 marginal-gain distribution (n={report.n_candidates} positive)"
    )
    table.add_column("percentile")
    table.add_column("marginal EP gain", justify="right")
    for name, value in report.percentiles.items():
        table.add_row(name, f"{value:.1f}")
    console.print(table)

    proposed = Table(title="Proposed tier thresholds (by rarity)")
    proposed.add_column("tier")
    proposed.add_column("rarity")
    proposed.add_column("threshold", justify="right")
    for name, rarity in (
        ("must_have", "top 15%"),
        ("strong_upgrade", "top 30%"),
        ("solid_upgrade", "top 50%"),
    ):
        proposed.add_row(name, rarity, f"{report.proposed[name]:.1f}")
    console.print(proposed)
    console.print(
        "[dim]Read-only. Apply these by editing config.py / bidding_strategy.py.[/dim]"
    )
```

### Required prerequisite: `market_scores` is not currently returned

`Trader.get_ep_recommendations` returns a dict whose verified keys are
`buy_recs, trade_pairs, sell_recs, squad_scores, lineup_map, budget, squad_size, squad_players, market_players, competitor_player_ids` (plus a performance map).
**It scores every market player internally but does not return those scores.**

So before the command above can work, add `"market_scores": {s.player_id: s for s in market_scores}` to that return dict (`trader.py`, around line 439). It is a
purely additive change — a new key no existing caller reads.

**Why not use `buy_recs` instead**, which already carries `marginal_ep_gain`:
`recommend_buys` returns only the **top-N, already filtered and ranked**, so its
gains sample the good tail of the distribution. Deriving thresholds from it would
push every tier upward — the exact opposite of the correction this task exists to
make. Thresholds must be measured over *all* market candidates.

Verify the additive change breaks nothing: `uv run pytest -q` before proceeding.

- [ ] **Step 6: Run it against the live league and record the output**

```bash
uv run rehoboam derive-thresholds
```

**Record the full table in your report** — Task 3 applies these numbers, so they are this task's real deliverable. If the distribution has very few positive candidates (say under 20), say so: thresholds derived from a thin sample are weak evidence, and Task 3 should widen the rarity bands rather than pretend to precision.

- [ ] **Step 7: Run the full suite and commit**

```bash
uv run pytest -q
git add rehoboam/scoring/v2/thresholds.py tests/test_scoring_v2/test_thresholds.py rehoboam/cli.py
git commit -m "feat(scoring): derive-thresholds command measuring v2 marginal-gain rarity"
```

______________________________________________________________________

## Task 3: Migrate the consumers

**Files:**

- Modify: `rehoboam/trader.py:390,429` (call sites), `rehoboam/config.py:111,115`, `rehoboam/bidding_strategy.py` (tier constants)
- Create: `tests/test_v2_integration.py`

**Interfaces:**

- Consumes: `score_player_v2` (Task 1), the measured thresholds from Task 2

### The thresholds are pre-season estimates and cannot be validated yet

`derive-thresholds` measured **n = 0**: the market holds 21 listings but none are
`is_kickbase_seller()`, and **there will be no purchasable listings until the
season starts** (2026-08-28). The command is correct and useful — it simply has
nothing to measure until then.

So the numbers below come from a controller measurement over the **full
473-player universe** against a synthetic mid-table 15-man squad (legal shape,
players drawn from the middle third of the EP distribution per position; best-11
total 277). That is arguably a better basis than one arbitrary day's listings,
since thresholds should hold all season rather than reflect a single snapshot.

|               | p25  | p50  | p70  | p85  | p95  | max   |
| ------------- | ---- | ---- | ---- | ---- | ---- | ----- |
| marginal gain | 13.7 | 43.1 | 52.6 | 69.3 | 82.0 | 176.2 |

309 of 473 candidates (65%) show a positive gain. For comparison, the current
`must_have >= 20` sits **below the median** — it would fire on most candidates.

**Two consequences for how this task implements them:**

- **They must be tunable without a deploy.** No live-market validation is
  possible until after kickoff, so the first real evidence arrives mid-season.
  Every threshold becomes a `Settings` field readable from `.env`.
- **Marginal gain is relative to your own squad.** A weak squad finds many
  improvements; a strong one finds few, so a fixed threshold is not a fixed
  standard — it drifts as the squad changes. `must_have` may rarely fire late in
  a successful season, which will look like the bot going passive when it is
  behaving as designed. Say so in the field descriptions.

### What changes

1. **`trader.py:390` and `trader.py:429`** — swap `score_player(data, calibration_multiplier=_calibration_for(player))` for `score_player_v2(data)`. The calibration multiplier is dropped deliberately (see Task 1's rationale). If `_calibration_for` becomes unused, remove it and its `get_position_calibration_multiplier` call.

1. **`config.py:111` `min_expected_points_to_buy`** (30.0) and **`config.py:115` `min_ep_upgrade_threshold`** (5.0). `min_ep_upgrade_threshold` is the floor for recommending a buy at all, so the `solid_upgrade` value is the natural choice — set it to **40.0** (rounded from p50 = 43.1). `min_expected_points_to_buy` is an *absolute* EP floor rather than a marginal one; the new EP median is 34.8, so **35.0** preserves its original intent of "do not buy a player who is not at least mid-table". Update each `description` to state the scale is real points, the derivation date and method, and that it is a **pre-season estimate awaiting live-market validation**.

1. **Bid tiers become `Settings` fields**, not module constants: `bid_tier_must_have` (**70.0**), `bid_tier_strong_upgrade` (**53.0**), `bid_tier_solid_upgrade` (**43.0**) — rounded from p85 / p70 / p50 of the measured distribution. Replace `bidding_strategy.py`'s inline `>= 20` / `>= 10` / `>= 5` with reads from settings. **This is what lets the thresholds be re-tuned from `.env` once the market opens, rather than shipping a new build.**

1. **`decision.py:345`** — there is a fallback `marginal = ps.expected_points` where the formation-aware path is not used. That substitutes an *absolute* EP for a *marginal* gain, which was already a scale mismatch on the old index and is a larger one now. Investigate when it fires; if it is reachable, either fix it to compute a real marginal gain or document why absolute EP is acceptable there. **Report what you find** — do not silently leave it.

- [ ] **Step 1: Write the failing integration test**

```python
"""Integration: the bot's live scoring path uses v2 and real point units."""

from __future__ import annotations

from rehoboam.config import Settings


def test_buy_gate_is_on_the_real_points_scale():
    """The old 30.0 was a 0-100 index value; real-points EP has a median of ~35,
    so an unchanged 30.0 would be almost no filter at all."""
    settings = Settings()
    assert (
        settings.min_expected_points_to_buy != 30.0
    ), "still the old 0-100 index value — re-derive from the v2 distribution"


def test_upgrade_threshold_is_documented_as_real_points():
    field = Settings.model_fields["min_ep_upgrade_threshold"]
    assert "real points" in (field.description or "").lower()


def test_bid_tiers_are_named_constants_not_magic_numbers():
    from rehoboam import bidding_strategy

    assert hasattr(bidding_strategy, "TIER_MUST_HAVE")
    assert hasattr(bidding_strategy, "TIER_STRONG_UPGRADE")
    assert hasattr(bidding_strategy, "TIER_SOLID_UPGRADE")
    assert (
        bidding_strategy.TIER_MUST_HAVE
        > bidding_strategy.TIER_STRONG_UPGRADE
        > bidding_strategy.TIER_SOLID_UPGRADE
    )


def test_trader_scores_with_v2():
    """The live scoring path must call the v2 adapter, not the v1 scorer."""
    import inspect

    from rehoboam import trader

    source = inspect.getsource(trader)
    assert "score_player_v2" in source
    assert "calibration_multiplier" not in source, (
        "REH-20's position calibration was fitted against the old index and "
        "must not be applied to real points"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_v2_integration.py -v`
Expected: FAIL — the thresholds are unchanged and `trader.py` still calls `score_player`.

- [ ] **Step 3: Apply the migration**

Make changes 1–4 above, using the numbers Task 2 measured.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_v2_integration.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the whole suite and fix what the scale change breaks**

Run: `uv run pytest -q`

Existing tests may assert on old-scale EP values. **Each failure is a decision, not a chore:** if a test asserted a 0–100 index value, update it to the real-points equivalent and say so in your report. If a test fails because behaviour genuinely changed, stop and report rather than adjusting the assertion to match — that distinction is the whole point of this step.

- [ ] **Step 6: Dry-run against the live league**

```bash
uv run rehoboam status
```

This exercises the full pipeline read-only. Confirm EP values look like real points (tens to low hundreds, not 0–100 clustered near 72) and that buy recommendations are not wildly more or fewer than before. **Record the output in your report.**

- [ ] **Step 7: Commit**

```bash
git add -u
git commit -m "feat(scoring): migrate live scoring to v2 in real point units"
```

______________________________________________________________________

## Task 4: Delete the legacy scoring path

**Files:**

- Modify: `rehoboam/auto_trader.py` (remove `_legacy_expected_points` and its call site at ~1317)
- Delete: `rehoboam/expected_points.py`, `rehoboam/value_calculator.py`, `tests/test_expected_points.py`

### Why these survived until now

Week 1 deliberately left them alive. An earlier draft listed them as dead; they are not — `auto_trader.py:1317` calls `_legacy_expected_points`, which imports `expected_points.calculate_expected_points` (`auto_trader.py:1350`), which imports `value_calculator.PlayerValue` (`expected_points.py:48`). That is the lineup fallback for players the EP pipeline did not score.

They carry the **same saturation defect** — `expected_points.py` has its own `min(avg_points * 2, 40)` — so the fallback path has been exactly as blind as the main one. They die here, now that the thing they fall back to is fixed.

- [ ] **Step 1: Verify the fallback is genuinely replaceable**

```bash
grep -n "_legacy_expected_points" -B 8 rehoboam/auto_trader.py
```

Understand when it fires: it fills `ep_scores` for a squad player the EP pipeline did not score (a mid-session purchase, or an upstream failure). Under v2 the replacement is `score_player_v2` on the same `PlayerData`, or — if assembling `PlayerData` there is impractical — `compose_ep` directly with `last_played_status`.

**If you cannot replace it cleanly, stop and report.** Deleting the fallback without a replacement would leave those players unscored, and an unscored player silently drops out of lineup selection — which is precisely the empty-slot failure that cost −100 three times last season.

- [ ] **Step 2: Write the failing test**

```python
"""The legacy scoring path is gone and its fallback is served by v2."""

from __future__ import annotations

import importlib

import pytest


def test_legacy_modules_are_deleted():
    for name in ("rehoboam.expected_points", "rehoboam.value_calculator"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


def test_auto_trader_has_no_legacy_fallback():
    import inspect

    from rehoboam import auto_trader

    source = inspect.getsource(auto_trader)
    assert "_legacy_expected_points" not in source
    assert "calculate_expected_points" not in source


def test_a_player_missing_from_the_pipeline_still_gets_scored():
    """The fallback's job: an unscored player must not silently vanish from
    lineup selection — that is the -100 empty-slot failure mode."""
    from rehoboam.scoring.v2.adapter import compose_ep
    from rehoboam.scoring.v2.coefficients import load_coefficients

    availability, rate, _ = load_coefficients()
    ep = compose_ep("never-seen-player", None, "Midfielder", availability, rate)
    assert ep > 0.0, "cold-start fallback must produce a usable score"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_legacy_removal.py -v`
Expected: FAIL — the modules still import.

- [ ] **Step 4: Replace the fallback, then delete**

Replace `_legacy_expected_points`'s body with a v2 call, verify the suite, *then*:

```bash
git rm rehoboam/expected_points.py rehoboam/value_calculator.py tests/test_expected_points.py
```

- [ ] **Step 5: Verify nothing still references them**

```bash
grep -rn "expected_points\b" rehoboam --include="*.py" | grep -v "scoring/\|\.expected_points\b" | grep import
grep -rn "value_calculator\|PlayerValue" rehoboam tests --include="*.py"
```

Expected: no output. Also confirm the Azure entrypoint still imports — it is not covered by the test suite, so a broken import there would leave the suite green and production broken:

```bash
uv run --extra azure python -c "import deploy.azure_function.function_app"
```

- [ ] **Step 6: Run the full suite and commit**

```bash
uv run pytest -q
git add -A
git commit -m "chore: delete the legacy scoring path (expected_points, value_calculator)"
```

______________________________________________________________________

## Self-Review

**Ticket coverage.** REH-55 asks for: real point units end-to-end (Tasks 1 and 3), every downstream consumer of the 0–100 scale updated (Task 3), and the legacy modules deleted (Task 4). Task 2 is not in the ticket text but is a precondition for Task 3 — migrating the scale without re-deriving the thresholds would ship a bot that classifies routine upgrades as must-haves.

**Deliberately out of scope, with the ticket that owns each:** context multipliers — fixture, home/away, DGW beyond the flat ×1.8 — are REH-54; the baseline comparison and ship decision are REH-56; serving-time `P(status)` overrides need within-status renormalisation and belong with REH-54 or their own ticket; `predicted_eps.marginal_ep_gain` being NULL deserves its own ticket alongside `transfer_pnl` and `winning_overbid_pct`.

**Known risk this plan carries.** Task 1 zeroes `PlayerScore`'s v1 decomposition fields. If any consumer reads them for anything other than display, behaviour changes silently. Task 3 Step 5 is where that surfaces — hence the instruction to treat each failure as a decision rather than adjusting assertions to match.

**Placeholder scan.** No TBDs. Task 2 Step 5's note about confirming `ep_result` field names is an explicit instruction to verify rather than guess, not a gap. Task 3 item 4 (`decision.py:345`) is a genuine unknown with a required report rather than a silent skip.

**Type consistency.** `score_player_v2(data: PlayerData) -> PlayerScore` matches the signature `trader.py` already calls. `compose_ep` and `last_played_status` are used identically in Tasks 1 and 4. `ThresholdReport`'s fields match what the CLI renders. `PLAYED_STATUSES` is imported from `features.py` in both the adapter and thresholds, never redefined.
