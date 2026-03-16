# EP Pipeline, Bidding & Learning Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the EP scoring pipeline, wire it into bidding decisions driven by marginal EP gain, add matchday outcome learning, and remove `--detailed` mode from `analyze`.

**Architecture:** Three-layer scoring pipeline (DataCollector → Scorer → DecisionEngine) feeds into EP-aware SmartBidding. BidLearner gains matchday outcome tracking for feedback. The `trade` command keeps the old `value_score` path untouched.

**Tech Stack:** Python 3.10+, dataclasses, SQLite, pytest, existing Kickbase API client

**Specs:**

- `docs/superpowers/specs/2026-03-11-ep-scoring-pipeline-design.md`
- `docs/superpowers/specs/2026-03-16-ep-bidding-and-learning-design.md`

______________________________________________________________________

## File Structure

### New files

| File                                   | Responsibility                                                                                                                                                   |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `rehoboam/scoring/__init__.py`         | Package exports: `PlayerScore`, `DataQuality`, `score_player`, `DataCollector`, `DecisionEngine`                                                                 |
| `rehoboam/scoring/models.py`           | Dataclasses: `DataQuality`, `PlayerScore`, `PlayerData`, `BuyRecommendation`, `SellRecommendation`, `TradePair`, `MarginalEPResult`, `SellPlan`, `SellPlanEntry` |
| `rehoboam/scoring/scorer.py`           | `score_player(PlayerData) -> PlayerScore` pure function + utility functions (consistency, minutes)                                                               |
| `rehoboam/scoring/collector.py`        | `DataCollector.collect()` — assembles `PlayerData` from pre-fetched API data                                                                                     |
| `rehoboam/scoring/decision.py`         | `DecisionEngine` — buy/sell/lineup/trade-pair + `calculate_marginal_ep()` + `build_sell_plan()`                                                                  |
| `tests/test_scoring/__init__.py`       | Test package                                                                                                                                                     |
| `tests/test_scoring/test_models.py`    | Tests for dataclass construction and edge cases                                                                                                                  |
| `tests/test_scoring/test_scorer.py`    | Tests for scoring formula, each component, data quality grading, DGW                                                                                             |
| `tests/test_scoring/test_collector.py` | Tests for data assembly and missing-field detection                                                                                                              |
| `tests/test_scoring/test_decision.py`  | Tests for buy/sell/lineup/trade-pair logic, roster awareness, quality gates, marginal EP, sell plans                                                             |
| `tests/test_ep_bidding.py`             | Tests for `calculate_ep_bid()`, EP-based tiers, budget ceiling, sell plan integration                                                                            |
| `tests/test_matchday_outcomes.py`      | Tests for `record_matchday_outcome()`, `get_ep_accuracy_factor()`, outcome quality                                                                               |

### Modified files

| File                           | Change                                                                                                                 |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| `rehoboam/config.py`           | Add `min_expected_points_to_buy` and `min_ep_upgrade_threshold` settings                                               |
| `rehoboam/bidding_strategy.py` | Add `calculate_ep_bid()` method, update `BidRecommendation` dataclass                                                  |
| `rehoboam/bid_learner.py`      | Add `matchday_outcomes` table, `record_matchday_outcome()`, `get_ep_accuracy_factor()`, `get_ep_recommended_overbid()` |
| `rehoboam/trader.py`           | New `display_ep_action_plan()` flow using scoring pipeline + EP bidding                                                |
| `rehoboam/compact_display.py`  | New `display_ep_action_plan()` method for EP-based display with EP Gain, Sell Plan columns                             |
| `rehoboam/cli.py`              | Remove `--detailed` mode, wire `analyze` to new pipeline, add matchday outcome recording                               |

______________________________________________________________________

## Chunk 1: Scoring Models and Scorer

### Task 1: Data models (`scoring/models.py`)

**Files:**

- Create: `rehoboam/scoring/__init__.py`

- Create: `rehoboam/scoring/models.py`

- Create: `tests/test_scoring/__init__.py`

- Create: `tests/test_scoring/test_models.py`

- [ ] **Step 1: Write tests for DataQuality and PlayerScore construction**

```python
# tests/test_scoring/test_models.py
"""Tests for scoring data models."""

from rehoboam.scoring.models import DataQuality, PlayerScore, PlayerData
from rehoboam.kickbase_client import MarketPlayer


def _make_player(**overrides) -> MarketPlayer:
    """Create a test MarketPlayer with sensible defaults."""
    defaults = {
        "id": "p1",
        "first_name": "Test",
        "last_name": "Player",
        "position": "Midfielder",
        "team_id": "t1",
        "team_name": "Test FC",
        "price": 5_000_000,
        "market_value": 5_000_000,
        "points": 100,
        "average_points": 12.0,
        "status": 0,
    }
    defaults.update(overrides)
    return MarketPlayer(**defaults)


class TestDataQuality:
    def test_grade_a(self):
        dq = DataQuality(
            grade="A",
            games_played=12,
            consistency=0.8,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        )
        assert dq.grade == "A"
        assert dq.warnings == []

    def test_grade_f_has_warnings(self):
        dq = DataQuality(
            grade="F",
            games_played=0,
            consistency=0.0,
            has_fixture_data=False,
            has_lineup_data=False,
            warnings=["No games played"],
        )
        assert dq.grade == "F"
        assert len(dq.warnings) == 1


class TestPlayerScore:
    def test_construction_with_all_fields(self):
        dq = DataQuality(
            grade="B",
            games_played=7,
            consistency=0.6,
            has_fixture_data=True,
            has_lineup_data=False,
            warnings=[],
        )
        ps = PlayerScore(
            player_id="p1",
            expected_points=55.0,
            data_quality=dq,
            base_points=24.0,
            consistency_bonus=9.0,
            lineup_bonus=10.0,
            fixture_bonus=5.0,
            form_bonus=2.0,
            minutes_bonus=5.0,
            dgw_multiplier=1.0,
            is_dgw=False,
            next_opponent="Bayern",
            notes=[],
            current_price=5_000_000,
            market_value=5_000_000,
        )
        assert ps.expected_points == 55.0
        assert ps.player_id == "p1"

    def test_dgw_player(self):
        dq = DataQuality(
            grade="A",
            games_played=15,
            consistency=0.9,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        )
        ps = PlayerScore(
            player_id="p2",
            expected_points=126.0,
            data_quality=dq,
            base_points=40.0,
            consistency_bonus=13.5,
            lineup_bonus=20.0,
            fixture_bonus=0.0,
            form_bonus=0.0,
            minutes_bonus=0.0,
            dgw_multiplier=1.8,
            is_dgw=True,
            next_opponent=None,
            notes=["DOUBLE GAMEWEEK"],
            current_price=10_000_000,
            market_value=10_000_000,
        )
        assert ps.is_dgw is True
        assert ps.dgw_multiplier == 1.8
```

Also create empty `tests/test_scoring/__init__.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scoring/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rehoboam.scoring'`

- [ ] **Step 3: Implement models**

```python
# rehoboam/scoring/__init__.py
"""EP-first scoring pipeline."""

# rehoboam/scoring/models.py
"""Data models for the EP scoring pipeline."""

from dataclasses import dataclass, field
from rehoboam.kickbase_client import MarketPlayer
from rehoboam.matchup_analyzer import TeamStrength


@dataclass
class DataQuality:
    """Quality assessment of the data used for scoring."""

    grade: str  # "A", "B", "C", "F"
    games_played: int
    consistency: float  # 0-1
    has_fixture_data: bool
    has_lineup_data: bool
    warnings: list[str]


@dataclass
class PlayerScore:
    """Scored player — the ONE number driving all decisions."""

    player_id: str
    expected_points: float  # 0-180 scale (DGW can exceed 100)
    data_quality: DataQuality

    # Components (for transparency/display)
    base_points: float
    consistency_bonus: float
    lineup_bonus: float
    fixture_bonus: float
    form_bonus: float
    minutes_bonus: float
    dgw_multiplier: float  # 1.0 normally, 1.8 for DGW

    # Context
    is_dgw: bool
    next_opponent: str | None
    notes: list[str]

    # Price context
    current_price: int
    market_value: int


@dataclass
class PlayerData:
    """Raw data assembled by DataCollector for a single player."""

    player: MarketPlayer
    performance: dict | None
    player_details: dict | None
    team_strength: TeamStrength | None
    opponent_strength: TeamStrength | None
    is_dgw: bool  # Simplified from spec's DoubleGameweekInfo — DGW detection is out of scope
    missing: list[str] = field(default_factory=list)


@dataclass
class BuyRecommendation:
    """EP-based buy recommendation."""

    score: PlayerScore
    marginal_ep_gain: float  # How much this improves best-11
    replaces_player_id: str | None
    replaces_player_name: str | None
    roster_impact: str  # "fills_gap", "upgrade", "additional"
    reason: str


@dataclass
class SellRecommendation:
    """EP-based sell recommendation."""

    score: PlayerScore
    expendability: float  # 0-100 (higher = more expendable)
    is_protected: bool
    protection_reason: str | None
    reason: str


@dataclass
class TradePair:
    """Sell→Buy swap recommendation."""

    buy: BuyRecommendation
    sell: SellRecommendation
    net_cost: int  # buy price - sell recovery
    ep_gain: float  # Net EP improvement


@dataclass
class MarginalEPResult:
    """Result of marginal EP gain calculation for a potential buy."""

    player_id: str
    expected_points: float
    current_squad_ep: float
    new_squad_ep: float
    marginal_ep_gain: float
    replaces_player_id: str | None
    replaces_player_name: str | None
    replaces_player_ep: float


@dataclass
class SellPlanEntry:
    """Single player in a sell plan."""

    player_id: str
    player_name: str
    expected_sell_value: int  # market_value * 0.95
    player_ep: float
    is_in_best_11: bool


@dataclass
class SellPlan:
    """Plan to recover budget after an expensive purchase."""

    players_to_sell: list[SellPlanEntry]
    total_recovery: int
    net_budget_after: int  # budget - bid_amount + total_recovery
    is_viable: bool  # net_budget_after >= 0
    ep_impact: float
    reasoning: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scoring/test_models.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add rehoboam/scoring/__init__.py rehoboam/scoring/models.py tests/test_scoring/__init__.py tests/test_scoring/test_models.py
git commit -m "feat(scoring): add EP pipeline data models"
```

______________________________________________________________________

### Task 2: Scorer (`scoring/scorer.py`)

**Files:**

- Create: `rehoboam/scoring/scorer.py`
- Create: `tests/test_scoring/test_scorer.py`

The scorer is a pure function — no API calls, no side effects. It reimplements the consistency and minutes extraction from `value_calculator.py` to avoid cross-dependency.

- [ ] **Step 1: Write tests for scoring formula components**

```python
# tests/test_scoring/test_scorer.py
"""Tests for the EP scorer — pure function, no API calls."""

from rehoboam.scoring.scorer import (
    score_player,
    _extract_consistency,
    _extract_minutes_trend,
    _grade_data_quality,
)
from rehoboam.scoring.models import PlayerData
from rehoboam.kickbase_client import MarketPlayer


def _make_player(**overrides) -> MarketPlayer:
    defaults = {
        "id": "p1",
        "first_name": "Test",
        "last_name": "Player",
        "position": "Midfielder",
        "team_id": "t1",
        "team_name": "Test FC",
        "price": 5_000_000,
        "market_value": 5_000_000,
        "points": 100,
        "average_points": 12.0,
        "status": 0,
    }
    defaults.update(overrides)
    return MarketPlayer(**defaults)


def _make_player_data(
    player=None, performance=None, player_details=None, **kw
) -> PlayerData:
    if player is None:
        player = _make_player()
    return PlayerData(
        player=player,
        performance=performance,
        player_details=player_details,
        team_strength=None,
        opponent_strength=None,
        is_dgw=kw.get("is_dgw", False),
        missing=[],
    )


class TestBasePoints:
    def test_avg_20_gives_40_base(self):
        """avg_points=20 → base=min(20*2, 40)=40 (capped)."""
        player = _make_player(average_points=20.0)
        result = score_player(_make_player_data(player=player))
        assert result.base_points == 40.0

    def test_avg_5_gives_10_base(self):
        player = _make_player(average_points=5.0)
        result = score_player(_make_player_data(player=player))
        assert result.base_points == 10.0

    def test_avg_0_gives_0_base(self):
        player = _make_player(average_points=0.0)
        result = score_player(_make_player_data(player=player))
        assert result.base_points == 0.0


class TestLineupBonus:
    def test_starter_gets_plus_20(self):
        player = _make_player()
        details = {"prob": 1}
        result = score_player(_make_player_data(player=player, player_details=details))
        assert result.lineup_bonus == 20.0

    def test_unlikely_gets_minus_20(self):
        player = _make_player()
        details = {"prob": 4}
        result = score_player(_make_player_data(player=player, player_details=details))
        assert result.lineup_bonus == -20.0

    def test_no_details_gets_0(self):
        player = _make_player()
        result = score_player(_make_player_data(player=player))
        assert result.lineup_bonus == 0.0


class TestFormBonus:
    def test_hot_streak(self):
        """current_points / avg_points > 2.0 → +10."""
        player = _make_player(average_points=10.0, points=25)
        result = score_player(_make_player_data(player=player))
        assert result.form_bonus == 10.0

    def test_not_scoring(self):
        """current_points == 0 → -10."""
        player = _make_player(average_points=10.0, points=0)
        result = score_player(_make_player_data(player=player))
        assert result.form_bonus == -10.0


class TestDGW:
    def test_dgw_multiplies_score(self):
        player = _make_player(average_points=15.0)
        normal = score_player(_make_player_data(player=player, is_dgw=False))
        dgw = score_player(_make_player_data(player=player, is_dgw=True))
        assert dgw.expected_points > normal.expected_points
        assert dgw.dgw_multiplier == 1.8

    def test_dgw_clamped_to_180(self):
        """Even with DGW, score can't exceed 180."""
        player = _make_player(average_points=100.0, points=200)
        result = score_player(_make_player_data(player=player, is_dgw=True))
        assert result.expected_points <= 180.0


class TestDataQualityGrading:
    def test_grade_a(self):
        """10+ games, fixture + lineup data → A."""
        perf = {"m": [{"p": 10}] * 12}  # 12 games
        details = {"prob": 1}
        grade = _grade_data_quality(games_played=12, has_fixture=True, has_lineup=True)
        assert grade.grade == "A"

    def test_grade_f_halves_score(self):
        """0-1 games → F, score halved."""
        player = _make_player(average_points=20.0)
        # No performance data = 0 games
        result = score_player(_make_player_data(player=player))
        # Without performance data, grade should be F and score halved
        assert result.data_quality.grade == "F"
        # base_points would be 40, but total gets halved
        assert result.expected_points <= 20.0


class TestTotalScore:
    def test_score_clamped_to_0(self):
        """Very bad player can't go below 0."""
        player = _make_player(average_points=0.0, points=0)
        details = {"prob": 5}
        result = score_player(_make_player_data(player=player, player_details=details))
        assert result.expected_points >= 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scoring/test_scorer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rehoboam.scoring.scorer'`

- [ ] **Step 3: Implement scorer**

```python
# rehoboam/scoring/scorer.py
"""Pure scoring function — no API calls, no side effects."""

from .models import DataQuality, PlayerData, PlayerScore


def _extract_consistency(performance: dict) -> tuple[int, float | None]:
    """Extract games played and consistency score from performance data.

    Returns (games_played, consistency_score).
    Consistency is 1 - CV (coefficient of variation) of match points.
    """
    matches = performance.get("m", [])
    if not matches:
        return 0, None

    points_list = []
    for match in matches:
        if isinstance(match, dict):
            pts = match.get("p", 0)
            minutes = match.get("mp", 0) if isinstance(match, dict) else 0
            if minutes > 0:
                points_list.append(pts)

    games_played = len(points_list)
    if games_played < 2:
        return games_played, None

    import statistics

    mean = statistics.mean(points_list)
    if mean <= 0:
        return games_played, 0.0

    stdev = statistics.stdev(points_list)
    cv = stdev / mean
    consistency = max(0.0, min(1.0, 1.0 - cv))
    return games_played, consistency


def _extract_minutes_trend(performance: dict) -> tuple[str | None, float | None]:
    """Extract minutes trend from performance data.

    Compares first half vs second half of recent matches.
    Returns (trend, avg_minutes).
    """
    matches = performance.get("m", [])
    if not matches or len(matches) < 4:
        return None, None

    minutes_list = []
    for match in matches:
        if isinstance(match, dict):
            minutes_list.append(match.get("mp", 0))

    if not minutes_list:
        return None, None

    avg_minutes = sum(minutes_list) / len(minutes_list)
    mid = len(minutes_list) // 2
    first_half_avg = sum(minutes_list[:mid]) / mid if mid > 0 else 0
    second_half_avg = (
        sum(minutes_list[mid:]) / len(minutes_list[mid:])
        if len(minutes_list[mid:]) > 0
        else 0
    )

    if second_half_avg > first_half_avg * 1.15:
        return "increasing", avg_minutes
    elif second_half_avg < first_half_avg * 0.85:
        return "decreasing", avg_minutes
    else:
        return "stable", avg_minutes


def _grade_data_quality(
    games_played: int,
    has_fixture: bool,
    has_lineup: bool,
) -> DataQuality:
    """Assign data quality grade based on available data."""
    warnings = []

    if games_played <= 1:
        grade = "F"
        warnings.append(f"Only {games_played} game(s) played")
    elif games_played <= 4:
        grade = "C"
        if not has_fixture and not has_lineup:
            warnings.append("Missing fixture and lineup data")
    elif games_played <= 9:
        grade = "B" if (has_fixture or has_lineup) else "C"
    else:
        grade = "A" if (has_fixture and has_lineup) else "B"

    if not has_fixture:
        warnings.append("No fixture data")
    if not has_lineup:
        warnings.append("No lineup data")

    return DataQuality(
        grade=grade,
        games_played=games_played,
        consistency=0.0,  # Filled by caller
        has_fixture_data=has_fixture,
        has_lineup_data=has_lineup,
        warnings=warnings,
    )


def score_player(data: PlayerData) -> PlayerScore:
    """Score a player based on expected matchday points.

    Pure function — no API calls, no side effects.
    Scale: 0-180 (DGW players can exceed 100).
    """
    player = data.player
    avg_points = player.average_points
    current_points = player.points
    notes = []

    # 1. Base points (0-40) — PRIMARY DRIVER
    base_points = min(avg_points * 2, 40)

    # 2. Consistency bonus (-5 to +15)
    games_played = 0
    consistency = 0.0
    consistency_bonus = 0.0
    if data.performance:
        games_played, cons = _extract_consistency(data.performance)
        if cons is not None:
            consistency = cons
            if cons >= 0.7:
                consistency_bonus = 15.0  # Flat +15 for very consistent (per spec)
                notes.append("Very consistent")
            elif cons >= 0.3:
                consistency_bonus = cons * 15  # Scaled for moderate consistency
            else:
                consistency_bonus = -5
                notes.append("Inconsistent")

    # 3. Lineup probability (-20 to +20)
    lineup_bonus = 0.0
    has_lineup = False
    if data.player_details:
        prob = data.player_details.get("prob", 5)
        has_lineup = True
        if prob == 1:
            lineup_bonus = 20
            notes.append("Starter")
        elif prob == 2:
            lineup_bonus = 10
            notes.append("Rotation")
        elif prob == 3:
            lineup_bonus = 0
            notes.append("Bench")
        elif prob >= 4:
            lineup_bonus = -20
            notes.append("Unlikely to play")

    # 4. Fixture bonus (-10 to +15)
    fixture_bonus = 0.0
    has_fixture = data.opponent_strength is not None
    if has_fixture and data.team_strength:
        # Simple: compare team strengths
        our_strength = data.team_strength.strength_score
        opp_strength = data.opponent_strength.strength_score
        diff = our_strength - opp_strength
        fixture_bonus = max(-10, min(15, diff / 5))
        if fixture_bonus >= 5:
            notes.append("Easy fixture")
        elif fixture_bonus <= -5:
            notes.append("Hard fixture")

    # 5. Minutes trend (-10 to +10)
    minutes_bonus = 0.0
    if data.performance:
        trend, avg_mins = _extract_minutes_trend(data.performance)
        if trend == "increasing":
            minutes_bonus = 10
            notes.append("Minutes increasing")
        elif trend == "decreasing":
            minutes_bonus = -10
            notes.append("Minutes decreasing")
        elif trend == "stable" and avg_mins is not None and avg_mins < 30:
            minutes_bonus = -8
            notes.append("Rarely plays")

    # 6. Form bonus (-10 to +10)
    form_bonus = 0.0
    if avg_points > 0:
        form_ratio = current_points / avg_points
        if form_ratio > 2.0:
            form_bonus = 10
            notes.append("Hot streak")
        elif form_ratio > 1.3:
            form_bonus = 5
        elif form_ratio < 0.5 and current_points > 0:
            form_bonus = -5
            notes.append("Below average")
        elif current_points == 0:
            form_bonus = -10
            notes.append("Not scoring")

    # Data quality grading
    dq = _grade_data_quality(games_played, has_fixture, has_lineup)
    dq.consistency = consistency

    # Calculate total
    total = (
        base_points
        + consistency_bonus
        + lineup_bonus
        + fixture_bonus
        + minutes_bonus
        + form_bonus
    )

    # DGW multiplier
    dgw_multiplier = 1.8 if data.is_dgw else 1.0
    if data.is_dgw:
        total *= dgw_multiplier
        notes.append("DOUBLE GAMEWEEK")

    # Grade F penalty: halve score
    if dq.grade == "F":
        total *= 0.5

    # Clamp to 0-180
    total = max(0, min(180, total))

    return PlayerScore(
        player_id=player.id,
        expected_points=round(total, 1),
        data_quality=dq,
        base_points=base_points,
        consistency_bonus=round(consistency_bonus, 1),
        lineup_bonus=lineup_bonus,
        fixture_bonus=round(fixture_bonus, 1),
        form_bonus=form_bonus,
        minutes_bonus=minutes_bonus,
        dgw_multiplier=dgw_multiplier,
        is_dgw=data.is_dgw,
        next_opponent=None,  # Filled by collector if available
        notes=notes,
        current_price=player.price,
        market_value=player.market_value,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scoring/test_scorer.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add rehoboam/scoring/scorer.py tests/test_scoring/test_scorer.py
git commit -m "feat(scoring): add EP scorer with TDD"
```

______________________________________________________________________

### Task 3: DataCollector (`scoring/collector.py`)

**Files:**

- Create: `rehoboam/scoring/collector.py`

- Create: `tests/test_scoring/test_collector.py`

- [ ] **Step 1: Write tests for data collection and missing-field detection**

```python
# tests/test_scoring/test_collector.py
"""Tests for DataCollector — assembles PlayerData from pre-fetched API data."""

from rehoboam.scoring.collector import DataCollector
from rehoboam.kickbase_client import MarketPlayer
from rehoboam.matchup_analyzer import MatchupAnalyzer


def _make_player(**overrides) -> MarketPlayer:
    defaults = {
        "id": "p1",
        "first_name": "Test",
        "last_name": "Player",
        "position": "Midfielder",
        "team_id": "t1",
        "team_name": "Test FC",
        "price": 5_000_000,
        "market_value": 5_000_000,
        "points": 100,
        "average_points": 12.0,
        "status": 0,
    }
    defaults.update(overrides)
    return MarketPlayer(**defaults)


class TestDataCollector:
    def test_collect_with_all_data(self):
        collector = DataCollector(matchup_analyzer=MatchupAnalyzer())
        player = _make_player()
        perf = {"m": [{"p": 10, "mp": 90}] * 5}
        details = {"prob": 1, "tid": "t1"}

        result = collector.collect(
            player=player,
            performance=perf,
            player_details=details,
            team_profiles={},
        )

        assert result.player.id == "p1"
        assert result.performance == perf
        assert result.missing == []

    def test_collect_with_missing_performance(self):
        collector = DataCollector(matchup_analyzer=MatchupAnalyzer())
        player = _make_player()

        result = collector.collect(
            player=player,
            performance=None,
            player_details=None,
            team_profiles={},
        )

        assert "performance" in result.missing
        assert "player_details" in result.missing

    def test_collect_flags_missing_opponent(self):
        collector = DataCollector(matchup_analyzer=MatchupAnalyzer())
        player = _make_player()
        details = {"prob": 1, "tid": "t1"}

        result = collector.collect(
            player=player,
            performance=None,
            player_details=details,
            team_profiles={},
        )

        assert result.opponent_strength is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scoring/test_collector.py -v`
Expected: FAIL

- [ ] **Step 3: Implement collector**

```python
# rehoboam/scoring/collector.py
"""DataCollector — assembles PlayerData from pre-fetched API data."""

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.matchup_analyzer import MatchupAnalyzer

from .models import PlayerData


class DataCollector:
    """Assembles and validates player data for scoring.

    Does NOT call the API directly. Receives pre-fetched data from the caller.
    """

    def __init__(self, matchup_analyzer: MatchupAnalyzer):
        self.matchup_analyzer = matchup_analyzer

    def collect(
        self,
        player: MarketPlayer,
        performance: dict | None,
        player_details: dict | None,
        team_profiles: dict[str, dict],
    ) -> PlayerData:
        """Assemble PlayerData from pre-fetched API data."""
        missing = []

        if performance is None:
            missing.append("performance")
        if player_details is None:
            missing.append("player_details")

        # Extract team strengths from profiles
        team_strength = None
        opponent_strength = None

        if player_details:
            team_id = player_details.get("tid", "")
            if team_id and team_id in team_profiles:
                team_strength = self.matchup_analyzer.get_team_strength(
                    team_profiles[team_id]
                )

            # Get opponent from next matchup
            next_matchup = self.matchup_analyzer.get_next_matchup(player_details)
            if next_matchup and next_matchup.opponent_id:
                if next_matchup.opponent_id in team_profiles:
                    opponent_strength = self.matchup_analyzer.get_team_strength(
                        team_profiles[next_matchup.opponent_id]
                    )
                else:
                    missing.append("opponent_strength")
            else:
                missing.append("opponent_strength")

        return PlayerData(
            player=player,
            performance=performance,
            player_details=player_details,
            team_strength=team_strength,
            opponent_strength=opponent_strength,
            is_dgw=False,  # DGW detection is a separate feature
            missing=missing,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scoring/test_collector.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add rehoboam/scoring/collector.py tests/test_scoring/test_collector.py
git commit -m "feat(scoring): add DataCollector with TDD"
```

______________________________________________________________________

## Chunk 2: DecisionEngine (Marginal EP + Sell Plans)

### Task 4: DecisionEngine core (`scoring/decision.py`)

**Files:**

- Create: `rehoboam/scoring/decision.py`

- Create: `tests/test_scoring/test_decision.py`

- [ ] **Step 1: Write tests for marginal EP calculation**

```python
# tests/test_scoring/test_decision.py
"""Tests for DecisionEngine — buy/sell/lineup decisions from PlayerScores."""

from rehoboam.scoring.decision import DecisionEngine
from rehoboam.scoring.models import DataQuality, PlayerScore, MarginalEPResult, SellPlan
from rehoboam.kickbase_client import MarketPlayer
from rehoboam.config import POSITION_MINIMUMS


def _make_score(
    player_id: str,
    ep: float,
    position: str = "Midfielder",
    price: int = 5_000_000,
    market_value: int = 5_000_000,
) -> PlayerScore:
    """Create a PlayerScore for testing."""
    dq = DataQuality(
        grade="A",
        games_played=15,
        consistency=0.8,
        has_fixture_data=True,
        has_lineup_data=True,
        warnings=[],
    )
    return PlayerScore(
        player_id=player_id,
        expected_points=ep,
        data_quality=dq,
        base_points=ep * 0.5,
        consistency_bonus=5.0,
        lineup_bonus=10.0,
        fixture_bonus=0.0,
        form_bonus=0.0,
        minutes_bonus=0.0,
        dgw_multiplier=1.0,
        is_dgw=False,
        next_opponent=None,
        notes=[],
        current_price=price,
        market_value=market_value,
    )


def _make_player(player_id: str, position: str) -> MarketPlayer:
    return MarketPlayer(
        id=player_id,
        first_name="Test",
        last_name=player_id,
        position=position,
        team_id="t1",
        team_name="Test FC",
        price=5_000_000,
        market_value=5_000_000,
        points=100,
        average_points=12.0,
        status=0,
    )


class TestMarginalEP:
    def test_player_improves_best_11(self):
        """New player with higher EP than weakest starter → positive marginal gain."""
        # Squad: 11 midfielders scoring 30 each + 1 GK + 3 DEF + 1 FWD
        squad = []
        squad_scores = []
        # GK
        squad.append(_make_player("gk1", "Goalkeeper"))
        squad_scores.append(_make_score("gk1", 40.0, "Goalkeeper"))
        # 3 DEF
        for i in range(3):
            squad.append(_make_player(f"def{i}", "Defender"))
            squad_scores.append(_make_score(f"def{i}", 35.0, "Defender"))
        # 6 MID (weakest = 20 EP)
        for i in range(6):
            ep = 50.0 - i * 5  # 50, 45, 40, 35, 30, 25
            squad.append(_make_player(f"mid{i}", "Midfielder"))
            squad_scores.append(_make_score(f"mid{i}", ep, "Midfielder"))
        # 1 FWD
        squad.append(_make_player("fwd0", "Forward"))
        squad_scores.append(_make_score("fwd0", 45.0, "Forward"))

        engine = DecisionEngine()
        # Market player: midfielder with 55 EP (better than all current)
        candidate = _make_score("new_mid", 55.0, "Midfielder")
        candidate_player = _make_player("new_mid", "Midfielder")

        result = engine.calculate_marginal_ep(
            candidate_score=candidate,
            candidate_player=candidate_player,
            squad=squad,
            squad_scores=squad_scores,
        )

        assert result.marginal_ep_gain > 0
        assert result.replaces_player_id is not None

    def test_player_doesnt_crack_best_11(self):
        """Player worse than all starters → marginal gain = 0."""
        squad = []
        squad_scores = []
        squad.append(_make_player("gk1", "Goalkeeper"))
        squad_scores.append(_make_score("gk1", 40.0, "Goalkeeper"))
        for i in range(3):
            squad.append(_make_player(f"def{i}", "Defender"))
            squad_scores.append(_make_score(f"def{i}", 50.0, "Defender"))
        for i in range(6):
            squad.append(_make_player(f"mid{i}", "Midfielder"))
            squad_scores.append(_make_score(f"mid{i}", 60.0, "Midfielder"))
        squad.append(_make_player("fwd0", "Forward"))
        squad_scores.append(_make_score("fwd0", 55.0, "Forward"))

        engine = DecisionEngine()
        # Weak candidate: 20 EP
        candidate = _make_score("weak", 20.0, "Midfielder")
        candidate_player = _make_player("weak", "Midfielder")

        result = engine.calculate_marginal_ep(
            candidate_score=candidate,
            candidate_player=candidate_player,
            squad=squad,
            squad_scores=squad_scores,
        )

        assert result.marginal_ep_gain == 0


class TestSellPlan:
    def test_viable_sell_plan(self):
        """Sell plan recovers enough budget → viable."""
        squad = []
        squad_scores = []
        squad.append(_make_player("gk1", "Goalkeeper"))
        squad_scores.append(
            _make_score("gk1", 40.0, "Goalkeeper", market_value=3_000_000)
        )
        for i in range(3):
            squad.append(_make_player(f"def{i}", "Defender"))
            squad_scores.append(
                _make_score(f"def{i}", 35.0, "Defender", market_value=5_000_000)
            )
        for i in range(6):
            squad.append(_make_player(f"mid{i}", "Midfielder"))
            squad_scores.append(
                _make_score(
                    f"mid{i}", 50.0 - i * 5, "Midfielder", market_value=8_000_000
                )
            )
        squad.append(_make_player("fwd0", "Forward"))
        squad_scores.append(
            _make_score("fwd0", 45.0, "Forward", market_value=10_000_000)
        )

        engine = DecisionEngine()
        result = engine.build_sell_plan(
            bid_amount=20_000_000,
            current_budget=5_000_000,
            squad=squad,
            squad_scores=squad_scores,
            best_11_ids={
                "gk1",
                "def0",
                "def1",
                "def2",
                "mid0",
                "mid1",
                "mid2",
                "mid3",
                "mid4",
                "mid5",
                "fwd0",
            },
            displaced_player_id="mid5",
        )

        assert result.is_viable
        assert result.net_budget_after >= 0

    def test_sell_plan_not_viable_if_all_protected(self):
        """If all players are protected (at position minimum), sell plan is not viable."""
        # Minimal squad: exactly at minimums
        squad = [_make_player("gk1", "Goalkeeper")]
        squad_scores = [_make_score("gk1", 40.0, "Goalkeeper", market_value=3_000_000)]

        engine = DecisionEngine()
        result = engine.build_sell_plan(
            bid_amount=50_000_000,
            current_budget=1_000_000,
            squad=squad,
            squad_scores=squad_scores,
            best_11_ids={"gk1"},
            displaced_player_id=None,
        )

        assert not result.is_viable
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scoring/test_decision.py -v`
Expected: FAIL

- [ ] **Step 3: Implement DecisionEngine**

```python
# rehoboam/scoring/decision.py
"""DecisionEngine — buy/sell/lineup decisions from PlayerScores."""

from rehoboam.config import POSITION_MINIMUMS
from rehoboam.formation import select_best_eleven

from .models import (
    BuyRecommendation,
    MarginalEPResult,
    PlayerScore,
    SellPlan,
    SellPlanEntry,
    SellRecommendation,
    TradePair,
)


class DecisionEngine:
    """Makes buy/sell/lineup decisions based on PlayerScores."""

    def __init__(self, min_ep_to_buy: float = 30.0, min_ep_upgrade: float = 10.0):
        self.min_ep_to_buy = min_ep_to_buy
        self.min_ep_upgrade = min_ep_upgrade

    def calculate_marginal_ep(
        self,
        candidate_score: PlayerScore,
        candidate_player,  # MarketPlayer
        squad: list,
        squad_scores: list[PlayerScore],
    ) -> MarginalEPResult:
        """Calculate how much a candidate player improves the best-11 total EP."""
        # Current best-11
        score_map = {s.player_id: s.expected_points for s in squad_scores}
        current_best_11 = select_best_eleven(squad, score_map)
        current_total = sum(score_map.get(p.id, 0) for p in current_best_11)
        current_best_ids = {p.id for p in current_best_11}

        # Simulate adding candidate
        extended_squad = squad + [candidate_player]
        extended_scores = {
            **score_map,
            candidate_score.player_id: candidate_score.expected_points,
        }
        new_best_11 = select_best_eleven(extended_squad, extended_scores)
        new_total = sum(extended_scores.get(p.id, 0) for p in new_best_11)
        new_best_ids = {p.id for p in new_best_11}

        # Determine who was displaced
        marginal_gain = max(0, new_total - current_total)
        displaced_ids = current_best_ids - new_best_ids
        replaces_id = None
        replaces_name = None
        replaces_ep = 0.0

        if candidate_score.player_id in new_best_ids and displaced_ids:
            replaces_id = next(iter(displaced_ids))
            for p in squad:
                if p.id == replaces_id:
                    replaces_name = f"{p.first_name} {p.last_name}"
                    break
            replaces_ep = score_map.get(replaces_id, 0)

        return MarginalEPResult(
            player_id=candidate_score.player_id,
            expected_points=candidate_score.expected_points,
            current_squad_ep=current_total,
            new_squad_ep=new_total,
            marginal_ep_gain=round(marginal_gain, 1),
            replaces_player_id=replaces_id,
            replaces_player_name=replaces_name,
            replaces_player_ep=replaces_ep,
        )

    def build_sell_plan(
        self,
        bid_amount: int,
        current_budget: int,
        squad: list,
        squad_scores: list[PlayerScore],
        best_11_ids: set[str],
        displaced_player_id: str | None,
    ) -> SellPlan:
        """Build a sell plan to recover budget after an expensive purchase."""
        shortfall = bid_amount - current_budget
        if shortfall <= 0:
            return SellPlan(
                players_to_sell=[],
                total_recovery=0,
                net_budget_after=current_budget - bid_amount,
                is_viable=True,
                ep_impact=0.0,
                reasoning="Within budget, no sells needed",
            )

        # Build candidate sell list: bench players sorted by expendability
        score_map = {s.player_id: s for s in squad_scores}
        position_counts = {}
        for p in squad:
            pos = p.position
            position_counts[pos] = position_counts.get(pos, 0) + 1

        candidates = []
        for p in squad:
            pid = p.id
            ps = score_map.get(pid)
            if ps is None:
                continue

            # Check protection
            is_protected = False
            min_for_pos = POSITION_MINIMUMS.get(p.position, 0)
            if position_counts.get(p.position, 0) <= min_for_pos:
                is_protected = True

            is_in_best_11 = pid in best_11_ids
            is_displaced = pid == displaced_player_id

            # Displaced player is always a sell candidate (they're being replaced)
            if is_protected and not is_displaced:
                continue

            # Non-displaced best-11 starters are protected
            if is_in_best_11 and not is_displaced:
                continue

            sell_value = int(p.market_value * 0.95)
            candidates.append(
                SellPlanEntry(
                    player_id=pid,
                    player_name=f"{p.first_name} {p.last_name}",
                    expected_sell_value=sell_value,
                    player_ep=ps.expected_points,
                    is_in_best_11=is_in_best_11,
                )
            )

        # Sort: displaced player first, then by lowest EP (most expendable first)
        candidates.sort(
            key=lambda c: (
                0 if c.player_id == displaced_player_id else 1,
                c.player_ep,
            )
        )

        # Greedily select sells until shortfall covered
        selected = []
        total_recovery = 0
        ep_impact = 0.0
        for c in candidates:
            selected.append(c)
            total_recovery += c.expected_sell_value
            ep_impact -= c.player_ep  # Losing this player's EP
            if total_recovery >= shortfall:
                break

        net_after = current_budget - bid_amount + total_recovery
        is_viable = net_after >= 0

        if not is_viable:
            reasoning = f"Cannot recover enough: need €{shortfall:,}, can sell for €{total_recovery:,}"
        elif any(c.is_in_best_11 for c in selected):
            reasoning = "Viable but selling a starter (displaced by new buy)"
        else:
            reasoning = f"Sell {len(selected)} bench player(s) to fund purchase"

        return SellPlan(
            players_to_sell=selected,
            total_recovery=total_recovery,
            net_budget_after=net_after,
            is_viable=is_viable,
            ep_impact=ep_impact,
            reasoning=reasoning,
        )

    def recommend_buys(
        self,
        market_scores: list[PlayerScore],
        squad: list,
        squad_scores: list[PlayerScore],
        budget: int,
    ) -> list[BuyRecommendation]:
        """Rank market players by marginal EP gain. Filter by quality gates."""
        results = []
        for ms in market_scores:
            # Quality gates
            if ms.data_quality.grade == "F":
                continue
            avg_pts = ms.base_points / 2  # Reverse the min(avg*2, 40) formula
            if avg_pts < 20:
                continue

            # Find matching player for position info
            candidate_player = None
            for p in squad:  # Won't find market players here — need separate list
                pass
            # Note: caller must pass market_players list separately for position data

            if ms.expected_points < self.min_ep_to_buy:
                continue

            results.append(ms)

        # Sort by expected_points descending
        results.sort(key=lambda s: s.expected_points, reverse=True)
        return results[:10]  # Top 10

    def recommend_sells(
        self,
        squad: list,
        squad_scores: list[PlayerScore],
        best_11_ids: set[str],
    ) -> list[SellRecommendation]:
        """Rank squad players by expendability (lowest EP, not in best-11)."""
        score_map = {s.player_id: s for s in squad_scores}
        position_counts = {}
        for p in squad:
            position_counts[p.position] = position_counts.get(p.position, 0) + 1

        results = []
        for p in squad:
            ps = score_map.get(p.id)
            if ps is None:
                continue

            is_protected = False
            protection_reason = None
            min_for_pos = POSITION_MINIMUMS.get(p.position, 0)
            if position_counts.get(p.position, 0) <= min_for_pos:
                is_protected = True
                protection_reason = f"Min {p.position}"

            in_best_11 = p.id in best_11_ids
            # Expendability: inverse of EP, bonus for not being in best-11
            expendability = 100 - ps.expected_points + (0 if in_best_11 else 20)

            results.append(
                SellRecommendation(
                    score=ps,
                    expendability=expendability,
                    is_protected=is_protected,
                    protection_reason=protection_reason,
                    reason="Low EP, bench player" if not in_best_11 else "In best 11",
                )
            )

        results.sort(key=lambda r: r.expendability, reverse=True)
        return results

    def select_lineup(self, squad_scores: list[PlayerScore]) -> dict[str, float]:
        """Return {player_id: expected_points} for formation.select_best_eleven()."""
        return {s.player_id: s.expected_points for s in squad_scores}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scoring/test_decision.py -v`
Expected: All PASS

- [ ] **Step 5: Update `__init__.py` exports and commit**

```python
# rehoboam/scoring/__init__.py
"""EP-first scoring pipeline."""

from .models import (
    DataQuality,
    PlayerScore,
    PlayerData,
    MarginalEPResult,
    SellPlan,
    SellPlanEntry,
    BuyRecommendation,
    SellRecommendation,
    TradePair,
)
from .scorer import score_player
from .collector import DataCollector
from .decision import DecisionEngine

__all__ = [
    "DataQuality",
    "PlayerScore",
    "PlayerData",
    "MarginalEPResult",
    "SellPlan",
    "SellPlanEntry",
    "BuyRecommendation",
    "SellRecommendation",
    "TradePair",
    "score_player",
    "DataCollector",
    "DecisionEngine",
]
```

```bash
git add rehoboam/scoring/ tests/test_scoring/test_decision.py
git commit -m "feat(scoring): add DecisionEngine with marginal EP and sell plans"
```

______________________________________________________________________

## Chunk 3: EP-First Bidding

### Task 5: Config changes

**Files:**

- Modify: `rehoboam/config.py`

- [ ] **Step 1: Add EP config settings**

Add to `Settings` class in `config.py`:

```python
    # EP-First Settings
    min_expected_points_to_buy: float = Field(
        default=30.0,
        description="Minimum expected points to consider buying a player",
    )
    min_ep_upgrade_threshold: float = Field(
        default=10.0,
        description="Minimum EP gain to consider a market player an upgrade",
    )
```

- [ ] **Step 2: Run existing config tests to verify nothing broke**

Run: `pytest tests/test_config.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add rehoboam/config.py
git commit -m "feat(config): add EP-first settings"
```

______________________________________________________________________

### Task 6: Update BidRecommendation and add `calculate_ep_bid()` to SmartBidding

**Files:**

- Modify: `rehoboam/bidding_strategy.py`

- Create: `tests/test_ep_bidding.py`

- [ ] **Step 1: Write tests for EP-based bidding**

```python
# tests/test_ep_bidding.py
"""Tests for EP-based bidding — calculate_ep_bid()."""

from rehoboam.bidding_strategy import SmartBidding, BidRecommendation
from rehoboam.scoring.models import SellPlan, SellPlanEntry


class TestEPBidTiers:
    def test_must_have_tier(self):
        """marginal_ep_gain >= 20 → must_have tier, aggressive bid."""
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=10_000_000,
            market_value=12_000_000,
            expected_points=80.0,
            marginal_ep_gain=25.0,
            confidence=0.8,
            current_budget=20_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid > 0
        assert result.overbid_pct >= 10.0  # Must_have tier bonus

    def test_no_improvement_no_bid(self):
        """marginal_ep_gain == 0 → no bid."""
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=10_000_000,
            market_value=12_000_000,
            expected_points=20.0,
            marginal_ep_gain=0,
            confidence=0.5,
            current_budget=20_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid == 0

    def test_marginal_tier_conservative(self):
        """marginal_ep_gain 1-5 → marginal tier, minimal overbid."""
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=5_000_000,
            market_value=5_000_000,
            expected_points=35.0,
            marginal_ep_gain=3.0,
            confidence=0.6,
            current_budget=10_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid > 0
        # Marginal tier → 0% tier bonus, so overbid should be modest
        assert result.overbid_pct < 15.0


class TestBudgetCeiling:
    def test_bid_within_budget(self):
        """Bid should not exceed budget ceiling."""
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=15_000_000,
            market_value=15_000_000,
            expected_points=60.0,
            marginal_ep_gain=15.0,
            confidence=0.8,
            current_budget=20_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid <= 20_000_000

    def test_bid_with_sell_plan_extends_budget(self):
        """Sell plan recovery should extend available budget."""
        plan = SellPlan(
            players_to_sell=[
                SellPlanEntry("s1", "Sell Player", 10_000_000, 20.0, False),
            ],
            total_recovery=10_000_000,
            net_budget_after=5_000_000,
            is_viable=True,
            ep_impact=-20.0,
            reasoning="Sell bench player",
        )
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=12_000_000,
            market_value=12_000_000,
            expected_points=70.0,
            marginal_ep_gain=20.0,
            confidence=0.9,
            current_budget=5_000_000,
            sell_plan=plan,
        )
        # Budget ceiling = 5M + 10M = 15M, should be able to bid
        assert result.recommended_bid > 0
        assert result.sell_plan is not None


class TestMarketValueFloor:
    def test_never_bid_below_market_value(self):
        """Kickbase rule: bid >= market_value."""
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=3_000_000,
            market_value=5_000_000,
            expected_points=50.0,
            marginal_ep_gain=10.0,
            confidence=0.7,
            current_budget=10_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid >= 5_000_000


class TestBidRecommendationBackwardCompat:
    def test_max_profitable_bid_alias(self):
        """max_profitable_bid property should return budget_ceiling."""
        rec = BidRecommendation(
            base_price=5_000_000,
            recommended_bid=6_000_000,
            overbid_amount=1_000_000,
            overbid_pct=20.0,
            reasoning="test",
            budget_ceiling=10_000_000,
            sell_plan=None,
            marginal_ep_gain=15.0,
        )
        assert rec.max_profitable_bid == 10_000_000

    def test_backward_compat_construction(self):
        """Old callers can construct BidRecommendation without new fields."""
        rec = BidRecommendation(
            base_price=5_000_000,
            recommended_bid=6_000_000,
            overbid_amount=1_000_000,
            overbid_pct=20.0,
            reasoning="test",
            budget_ceiling=10_000_000,
        )
        assert rec.sell_plan is None
        assert rec.marginal_ep_gain == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ep_bidding.py -v`
Expected: FAIL

- [ ] **Step 3: Update `BidRecommendation` and implement `calculate_ep_bid()`**

Update `BidRecommendation` dataclass in `bidding_strategy.py`:

- Rename `max_profitable_bid` to `budget_ceiling`
- Add `sell_plan: SellPlan | None = None` (with default!)
- Add `marginal_ep_gain: float = 0.0` (with default!)
- Add `@property max_profitable_bid` alias for backward compat
- **Critical**: new fields MUST have defaults so existing `calculate_bid()` callers don't break

Also update the existing `calculate_bid()` to pass `budget_ceiling=` instead of `max_profitable_bid=` in its return statement.

Then add `calculate_ep_bid()` method to `SmartBidding` class. Keep `calculate_bid()` otherwise unchanged.

Key implementation details:

- Tier classification uses `marginal_ep_gain` thresholds (0/5/10/20)
- Budget ceiling = `current_budget + sell_plan.total_recovery`
- EP-proportional max: `max_bid_fraction = min(0.8, 0.2 + marginal_ep_gain / 50)`
- Market value floor: `max(recommended_bid, market_value * 1.01)`
- No bid when `marginal_ep_gain == 0`
- `sell_plan` attached to `BidRecommendation` when used

See spec for the full `calculate_ep_bid()` signature and formula. The implementation follows the spec exactly — read `docs/superpowers/specs/2026-03-16-ep-bidding-and-learning-design.md` Part 3 for details.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ep_bidding.py -v`
Expected: All PASS

- [ ] **Step 5: Run existing bidding tests to verify backward compat**

Run: `pytest tests/ -v -k "bid"` (if any existing bid tests exist)
Expected: All PASS — old `calculate_bid()` unchanged

- [ ] **Step 6: Commit**

```bash
git add rehoboam/bidding_strategy.py tests/test_ep_bidding.py
git commit -m "feat(bidding): add EP-based calculate_ep_bid() method"
```

______________________________________________________________________

## Chunk 4: Outcome-Based Learning

### Task 7: Matchday outcome tracking in BidLearner

**Files:**

- Modify: `rehoboam/bid_learner.py`

- Create: `tests/test_matchday_outcomes.py`

- [ ] **Step 1: Write tests for outcome recording and accuracy factor**

```python
# tests/test_matchday_outcomes.py
"""Tests for matchday outcome tracking and EP accuracy factor."""

import tempfile
from pathlib import Path
from rehoboam.bid_learner import BidLearner


class TestMatchdayOutcomeRecording:
    def test_record_and_retrieve(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            learner.record_matchday_outcome(
                player_id="p1",
                player_position="MID",
                matchday_date="2026-03-15",
                predicted_ep=60.0,
                actual_points=55.0,
                was_in_best_11=True,
                opponent_strength="Easy",
            )
            # Should not raise on duplicate (UNIQUE constraint uses INSERT OR REPLACE)
            learner.record_matchday_outcome(
                player_id="p1",
                player_position="MID",
                matchday_date="2026-03-15",
                predicted_ep=60.0,
                actual_points=55.0,
                was_in_best_11=True,
                opponent_strength="Easy",
            )

    def test_record_multiple_matchdays(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            for i in range(5):
                learner.record_matchday_outcome(
                    player_id="p1",
                    player_position="MID",
                    matchday_date=f"2026-03-{10+i}",
                    predicted_ep=50.0,
                    actual_points=45.0,
                )
            factor = learner.get_ep_accuracy_factor(player_id="p1")
            # accuracy = 45/50 = 0.9
            assert 0.85 <= factor <= 0.95


class TestEPAccuracyFactor:
    def test_returns_1_with_no_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            factor = learner.get_ep_accuracy_factor(player_id="unknown")
            assert factor == 1.0

    def test_capped_at_1(self):
        """Even if we underpredict, factor should not exceed 1.0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            for i in range(5):
                learner.record_matchday_outcome(
                    player_id="p1",
                    player_position="FWD",
                    matchday_date=f"2026-03-{10+i}",
                    predicted_ep=30.0,
                    actual_points=50.0,  # Underpredicted
                )
            factor = learner.get_ep_accuracy_factor(player_id="p1")
            assert factor == 1.0  # Capped

    def test_floored_at_0_5(self):
        """Severe overprediction capped at 0.5."""
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            for i in range(5):
                learner.record_matchday_outcome(
                    player_id="p1",
                    player_position="DEF",
                    matchday_date=f"2026-03-{10+i}",
                    predicted_ep=80.0,
                    actual_points=10.0,  # Severely overpredicted
                )
            factor = learner.get_ep_accuracy_factor(player_id="p1")
            assert factor == 0.5

    def test_position_fallback(self):
        """Falls back to position-level accuracy when player has < min_matchdays."""
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            # Record many outcomes for other MIDs
            for p in range(5):
                for i in range(5):
                    learner.record_matchday_outcome(
                        player_id=f"mid{p}",
                        player_position="MID",
                        matchday_date=f"2026-03-{10+i}",
                        predicted_ep=50.0,
                        actual_points=40.0,
                    )
            # Query for a new MID player with no data
            factor = learner.get_ep_accuracy_factor(
                player_id="new_mid",
                position="MID",
                min_matchdays=3,
            )
            # Position-level: 40/50 = 0.8
            assert 0.75 <= factor <= 0.85
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_matchday_outcomes.py -v`
Expected: FAIL

- [ ] **Step 3: Implement matchday outcome tracking**

Add to `BidLearner._init_db()`:

```python
# Add matchday_outcomes table
conn.execute("""
    CREATE TABLE IF NOT EXISTS matchday_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id TEXT NOT NULL,
        player_position TEXT NOT NULL,
        matchday_date TEXT NOT NULL,
        predicted_ep REAL NOT NULL,
        actual_points REAL NOT NULL,
        was_in_best_11 INTEGER DEFAULT 0,
        opponent_strength TEXT,
        purchase_price INTEGER,
        marginal_ep_gain_at_purchase REAL,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(player_id, matchday_date)
    )
""")
conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_matchday_player ON matchday_outcomes(player_id)"
)
conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_matchday_position ON matchday_outcomes(player_position)"
)
```

Add methods to `BidLearner`:

```python
def record_matchday_outcome(
    self,
    player_id: str,
    player_position: str,
    matchday_date: str,
    predicted_ep: float,
    actual_points: float,
    was_in_best_11: bool = False,
    opponent_strength: str | None = None,
    purchase_price: int | None = None,
    marginal_ep_gain_at_purchase: float | None = None,
) -> None:
    """Record actual matchday points vs predicted EP."""
    with sqlite3.connect(self.db_path) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO matchday_outcomes
            (player_id, player_position, matchday_date, predicted_ep,
             actual_points, was_in_best_11, opponent_strength,
             purchase_price, marginal_ep_gain_at_purchase)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                player_id,
                player_position,
                matchday_date,
                predicted_ep,
                actual_points,
                int(was_in_best_11),
                opponent_strength,
                purchase_price,
                marginal_ep_gain_at_purchase,
            ),
        )


def get_ep_accuracy_factor(
    self,
    player_id: str | None = None,
    position: str | None = None,
    min_matchdays: int = 3,
) -> float:
    """Return EP accuracy multiplier [0.5, 1.0] based on prediction history."""
    with sqlite3.connect(self.db_path) as conn:
        # Try player-specific first
        if player_id:
            cursor = conn.execute(
                """SELECT AVG(actual_points), AVG(predicted_ep), COUNT(*)
                FROM matchday_outcomes WHERE player_id = ?""",
                (player_id,),
            )
            row = cursor.fetchone()
            if row and row[2] >= min_matchdays and row[1] > 0:
                accuracy = row[0] / row[1]
                return max(0.5, min(1.0, accuracy))

        # Fall back to position-level
        if position:
            cursor = conn.execute(
                """SELECT AVG(actual_points), AVG(predicted_ep), COUNT(*)
                FROM matchday_outcomes WHERE player_position = ?""",
                (position,),
            )
            row = cursor.fetchone()
            if row and row[2] >= min_matchdays and row[1] > 0:
                accuracy = row[0] / row[1]
                return max(0.5, min(1.0, accuracy))

    return 1.0  # Default: trust predictions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_matchday_outcomes.py -v`
Expected: All PASS

- [ ] **Step 5: Implement `get_ep_recommended_overbid()` and `_get_won_player_outcome_quality()`**

Add to `BidLearner`:

```python
def _get_won_player_outcome_quality(self) -> float:
    """How well did players we won in auctions actually perform?

    Returns ratio of actual matchday points to predicted EP for won auctions.
    Clamped to [0.5, 1.2].
    """
    with sqlite3.connect(self.db_path) as conn:
        cursor = conn.execute("""SELECT AVG(mo.actual_points), AVG(mo.predicted_ep)
            FROM auction_outcomes ao
            JOIN matchday_outcomes mo ON ao.player_id = mo.player_id
            WHERE ao.won = 1""")
        row = cursor.fetchone()
        if row and row[0] is not None and row[1] and row[1] > 0:
            quality = row[0] / row[1]
            return max(0.5, min(1.2, quality))
    return 1.0


def get_ep_recommended_overbid(
    self,
    asking_price: int,
    marginal_ep_gain: float,
    market_value: int,
    budget_ceiling: int,
) -> dict:
    """EP-aware overbid recommendation using win rate + outcome quality."""
    result = {"recommended_overbid_pct": 10.0, "reason": "default"}

    with sqlite3.connect(self.db_path) as conn:
        cursor = conn.execute("""SELECT COUNT(*), SUM(CASE WHEN won THEN 1 ELSE 0 END),
                      AVG(CASE WHEN won THEN our_overbid_pct END),
                      AVG(CASE WHEN NOT won THEN our_overbid_pct END)
            FROM auction_outcomes
            WHERE timestamp > datetime('now', '-90 days')""")
        row = cursor.fetchone()
        total = row[0] or 0
        wins = row[1] or 0

        if total < 5:
            result["reason"] = f"only {total} auctions, using default"
            return result

        win_rate = wins / total
        if win_rate < 0.3:
            result["recommended_overbid_pct"] = 15.0
            result["reason"] = f"low win rate ({win_rate:.0%}), bidding more"
        elif win_rate < 0.5:
            result["recommended_overbid_pct"] = 12.0
            result["reason"] = f"below-average win rate ({win_rate:.0%})"
        elif win_rate > 0.8:
            result["recommended_overbid_pct"] = 8.0
            result["reason"] = f"high win rate ({win_rate:.0%}), may be overpaying"

    # Apply outcome quality factor
    outcome_quality = self._get_won_player_outcome_quality()
    result["recommended_overbid_pct"] *= outcome_quality
    if outcome_quality != 1.0:
        result["reason"] += f" | outcome quality: {outcome_quality:.2f}"

    # EP-gain-based minimum floors
    if marginal_ep_gain >= 20:
        result["recommended_overbid_pct"] = max(result["recommended_overbid_pct"], 12.0)
    elif marginal_ep_gain >= 10:
        result["recommended_overbid_pct"] = max(result["recommended_overbid_pct"], 10.0)

    return result
```

- [ ] **Step 6: Run all existing tests to verify nothing broke**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add rehoboam/bid_learner.py tests/test_matchday_outcomes.py
git commit -m "feat(learning): add matchday outcome tracking and EP accuracy factor"
```

______________________________________________________________________

## Chunk 5: CLI Wiring — Remove Detailed, Wire EP Pipeline

### Task 8: Remove `--detailed` mode from `analyze`

**Files:**

- Modify: `rehoboam/cli.py`

- [ ] **Step 1: Delete detailed mode flags and branch**

In `cli.py`, remove:

- Lines 55-82: `detailed`, `show_all`, `simple`, `show_risk`, `show_opportunity_cost`, `show_portfolio` parameters
- Lines 168-513: entire `if detailed:` branch
- Keep the `verbose` parameter (still useful for debugging)

The `analyze` function should end after `trader.display_compact_action_plan(league, market_analyses, current_budget)`.

Move the learning system update (lines ~436-506) to run AFTER `display_compact_action_plan()`:

```python
# After compact display, run learning updates silently
try:
    if factor_learner:
        trader.run_learning_update(league)
except Exception as e:
    if verbose:
        console.print(f"[yellow]Learning update warning: {e}[/yellow]")
```

- [ ] **Step 2: Verify CLI still works**

Run: `rehoboam analyze --help`
Expected: No `--detailed`, `--all`, `--simple`, `--risk`, `--opportunity-cost`, `--portfolio` flags

- [ ] **Step 3: Commit**

```bash
git add rehoboam/cli.py
git commit -m "refactor(cli): remove --detailed mode from analyze"
```

______________________________________________________________________

### Task 9: Wire EP pipeline into trader and display

**Files:**

- Modify: `rehoboam/trader.py`
- Modify: `rehoboam/compact_display.py`
- Modify: `rehoboam/cli.py`

This is the integration task — connecting the scoring pipeline to the display and bidding.

- [ ] **Step 1: Add `display_ep_action_plan()` to Trader**

Add a new method to `Trader` that:

1. Scores all squad players via `DataCollector` → `score_player()`
1. Scores market players the same way
1. Calculates `marginal_ep_gain` for each market player
1. Builds sell plans where needed
1. Calls `calculate_ep_bid()` for buy candidates
1. Passes results to `CompactDisplay.display_ep_action_plan()`

Read the spec flow diagram at `docs/superpowers/specs/2026-03-16-ep-bidding-and-learning-design.md` "Analyze command (new flow)" section. Follow it exactly.

- [ ] **Step 2: Add `display_ep_action_plan()` to CompactDisplay**

New method accepting `BuyRecommendation`, `SellRecommendation` lists. Table columns:

Buy table: Player | EP | EP Gain | Price | Bid | Sell Plan | Net Cost
Sell table: Sorted by expendability (low EP + not in best-11)

- [ ] **Step 3: Wire `cli.py` analyze to the new flow**

Replace `trader.display_compact_action_plan(league, market_analyses, current_budget)` with `trader.display_ep_action_plan(league, current_budget)`.

Also add matchday outcome recording after scoring:

```python
# Record matchday outcomes for learning
try:
    # Compare last predicted EP vs latest actual points for each squad player
    # This is lightweight — uses data already fetched
    trader.record_matchday_outcomes(league, squad_scores)
except Exception as e:
    if verbose:
        console.print(f"[dim]Warning: Could not record outcomes: {e}[/dim]")
```

- [ ] **Step 4: Test manually**

Run: `rehoboam analyze -v`
Expected: EP-based output with EP Gain column, sell plans where applicable

- [ ] **Step 5: Commit**

```bash
git add rehoboam/trader.py rehoboam/compact_display.py rehoboam/cli.py
git commit -m "feat: wire EP pipeline into analyze command with EP-based bidding"
```

______________________________________________________________________

### Task 10: Run full test suite and fix issues

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Run linting and type checks**

Run: `black rehoboam/ && ruff check rehoboam/ --fix && mypy rehoboam/ --ignore-missing-imports`
Expected: Clean

- [ ] **Step 3: Fix any issues found**

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: fix linting and test issues from EP pipeline integration"
```
