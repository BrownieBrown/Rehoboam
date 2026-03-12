# EP-First Scoring Pipeline Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace market-value-driven `value_score` with a unified expected-points pipeline for the `analyze` and `lineup` commands.

**Architecture:** Three-layer pipeline — DataCollector (assembles raw data), Scorer (pure function computing PlayerScore), DecisionEngine (buy/sell/lineup decisions). The `trade` command keeps the old `value_score` path untouched.

**Tech Stack:** Python 3.10+, dataclasses, pytest, existing Kickbase API client

**Spec:** `docs/superpowers/specs/2026-03-11-ep-scoring-pipeline-design.md`

______________________________________________________________________

## File Structure

### New files

| File                                   | Responsibility                                                                                                  |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `rehoboam/scoring/__init__.py`         | Package exports: `PlayerScore`, `DataQuality`, `score_player`, `DataCollector`, `DecisionEngine`                |
| `rehoboam/scoring/models.py`           | Dataclasses: `DataQuality`, `PlayerScore`, `PlayerData`, `BuyRecommendation`, `SellRecommendation`, `TradePair` |
| `rehoboam/scoring/scorer.py`           | `score_player(PlayerData) -> PlayerScore` pure function + utility functions for consistency/minutes extraction  |
| `rehoboam/scoring/collector.py`        | `DataCollector.collect()` — assembles `PlayerData` from pre-fetched API data, flags missing fields              |
| `rehoboam/scoring/decision.py`         | `DecisionEngine` — buy/sell/lineup/trade-pair recommendations from `PlayerScore` lists                          |
| `tests/test_scoring/__init__.py`       | Test package                                                                                                    |
| `tests/test_scoring/test_models.py`    | Tests for dataclass construction and edge cases                                                                 |
| `tests/test_scoring/test_scorer.py`    | Tests for scoring formula, each component, data quality grading, DGW                                            |
| `tests/test_scoring/test_collector.py` | Tests for data assembly and missing-field detection                                                             |
| `tests/test_scoring/test_decision.py`  | Tests for buy/sell/lineup/trade-pair logic, roster awareness, quality gates                                     |

### Modified files

| File                          | Change                                                                   |
| ----------------------------- | ------------------------------------------------------------------------ |
| `rehoboam/config.py`          | Add `min_expected_points_to_buy` and `min_ep_upgrade_threshold` settings |
| `rehoboam/trader.py`          | New `display_ep_action_plan()` flow using the scoring pipeline           |
| `rehoboam/compact_display.py` | New `display_ep_action_plan()` method for EP-based display               |
| `rehoboam/cli.py`             | Wire `analyze` and `lineup` to new pipeline                              |

______________________________________________________________________

## Chunk 1: Models and Scorer

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

from rehoboam.scoring.models import (
    DataQuality,
    PlayerScore,
    PlayerData,
    BuyRecommendation,
    SellRecommendation,
    TradePair,
)
from rehoboam.kickbase_client import MarketPlayer
from rehoboam.matchup_analyzer import DoubleGameweekInfo


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
            games_played=1,
            consistency=0.5,
            has_fixture_data=False,
            has_lineup_data=False,
            warnings=["Only 1 game played", "No fixture data"],
        )
        assert dq.grade == "F"
        assert len(dq.warnings) == 2


class TestPlayerScore:
    def test_construction(self):
        dq = DataQuality(
            grade="A",
            games_played=15,
            consistency=0.9,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        )
        ps = PlayerScore(
            player_id="p1",
            expected_points=75.0,
            data_quality=dq,
            base_points=30.0,
            consistency_bonus=10.0,
            lineup_bonus=20.0,
            fixture_bonus=5.0,
            form_bonus=5.0,
            minutes_bonus=5.0,
            dgw_multiplier=1.0,
            is_dgw=False,
            next_opponent="Bayern",
            notes=["Starter"],
            current_price=5_000_000,
            market_value=6_000_000,
        )
        assert ps.expected_points == 75.0
        assert ps.is_dgw is False

    def test_dgw_player(self):
        dq = DataQuality(
            grade="B",
            games_played=8,
            consistency=0.6,
            has_fixture_data=True,
            has_lineup_data=False,
            warnings=[],
        )
        ps = PlayerScore(
            player_id="p2",
            expected_points=126.0,
            data_quality=dq,
            base_points=40.0,
            consistency_bonus=10.0,
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
        assert ps.expected_points == 126.0


class TestPlayerData:
    def test_construction_with_missing(self):
        player = _make_player()
        pd = PlayerData(
            player=player,
            performance=None,
            player_details=None,
            team_strength=None,
            opponent_strength=None,
            dgw_info=DoubleGameweekInfo(is_dgw=False),
            missing=["performance", "player_details"],
        )
        assert pd.performance is None
        assert "performance" in pd.missing


class TestBuyRecommendation:
    def test_construction(self):
        dq = DataQuality(
            grade="A",
            games_played=15,
            consistency=0.9,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        )
        score = PlayerScore(
            player_id="p1",
            expected_points=70.0,
            data_quality=dq,
            base_points=30.0,
            consistency_bonus=10.0,
            lineup_bonus=20.0,
            fixture_bonus=5.0,
            form_bonus=5.0,
            minutes_bonus=0.0,
            dgw_multiplier=1.0,
            is_dgw=False,
            next_opponent="Dortmund",
            notes=[],
            current_price=5_000_000,
            market_value=5_000_000,
        )
        player = _make_player()
        rec = BuyRecommendation(
            player=player, score=score, roster_bonus=10.0, reason="fills_gap"
        )
        assert rec.roster_bonus == 10.0
        assert rec.effective_ep == 80.0  # 70 + 10 bonus


class TestTradePair:
    def test_net_cost(self):
        dq = DataQuality(
            grade="A",
            games_played=15,
            consistency=0.9,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        )
        buy_score = PlayerScore(
            player_id="p1",
            expected_points=70.0,
            data_quality=dq,
            base_points=30.0,
            consistency_bonus=10.0,
            lineup_bonus=20.0,
            fixture_bonus=5.0,
            form_bonus=5.0,
            minutes_bonus=0.0,
            dgw_multiplier=1.0,
            is_dgw=False,
            next_opponent=None,
            notes=[],
            current_price=8_000_000,
            market_value=8_000_000,
        )
        sell_score = PlayerScore(
            player_id="p2",
            expected_points=30.0,
            data_quality=dq,
            base_points=15.0,
            consistency_bonus=5.0,
            lineup_bonus=10.0,
            fixture_bonus=0.0,
            form_bonus=0.0,
            minutes_bonus=0.0,
            dgw_multiplier=1.0,
            is_dgw=False,
            next_opponent=None,
            notes=[],
            current_price=3_000_000,
            market_value=3_000_000,
        )
        buy_player = _make_player(id="p1", price=8_000_000, market_value=8_000_000)
        sell_player = _make_player(id="p2", price=3_000_000, market_value=3_000_000)
        tp = TradePair(
            buy_player=buy_player,
            sell_player=sell_player,
            buy_score=buy_score,
            sell_score=sell_score,
        )
        assert tp.net_cost == 5_000_000  # 8M - 3M
        assert tp.ep_gain == 40.0  # 70 - 30
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scoring/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.scoring'`

- [ ] **Step 3: Implement models**

```python
# rehoboam/scoring/__init__.py
"""EP-first scoring pipeline for matchday point optimization."""

from .models import (
    BuyRecommendation,
    DataQuality,
    PlayerData,
    PlayerScore,
    SellRecommendation,
    TradePair,
)

__all__ = [
    "BuyRecommendation",
    "DataQuality",
    "PlayerData",
    "PlayerScore",
    "SellRecommendation",
    "TradePair",
]
```

```python
# rehoboam/scoring/models.py
"""Data models for the EP-first scoring pipeline."""

from dataclasses import dataclass, field

from ..kickbase_client import MarketPlayer
from ..matchup_analyzer import DoubleGameweekInfo, TeamStrength


@dataclass
class DataQuality:
    """Data quality assessment for a player's score."""

    grade: str  # "A", "B", "C", "F"
    games_played: int
    consistency: float  # 0-1
    has_fixture_data: bool
    has_lineup_data: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class PlayerScore:
    """Unified expected points score for a player (0-180 scale)."""

    player_id: str
    expected_points: float  # The ONE number driving all decisions
    data_quality: DataQuality

    # Components (for transparency/display)
    base_points: float  # 0-40
    consistency_bonus: float  # -5 to +15
    lineup_bonus: float  # -20 to +20
    fixture_bonus: float  # -10 to +15
    form_bonus: float  # -10 to +10
    minutes_bonus: float  # -10 to +10
    dgw_multiplier: float  # 1.0 or 1.8

    # Context
    is_dgw: bool
    next_opponent: str | None
    notes: list[str] = field(default_factory=list)

    # Price context (needed by display and budget calculations)
    current_price: int = 0
    market_value: int = 0

    # Player context (needed by DecisionEngine without separate lookups)
    position: str = ""
    average_points: float = 0.0
    status: int = 0


@dataclass
class PlayerData:
    """Raw data bundle for scoring a player."""

    player: MarketPlayer
    performance: dict | None
    player_details: dict | None
    team_strength: TeamStrength | None
    opponent_strength: TeamStrength | None
    dgw_info: DoubleGameweekInfo
    missing: list[str] = field(default_factory=list)


@dataclass
class BuyRecommendation:
    """A recommended player to buy."""

    player: MarketPlayer
    score: PlayerScore
    roster_bonus: float  # +10 for fills_gap, 0 otherwise
    reason: str  # "fills_gap", "upgrade", "additional"

    @property
    def effective_ep(self) -> float:
        """EP used for sorting (includes roster bonus)."""
        return self.score.expected_points + self.roster_bonus


@dataclass
class SellRecommendation:
    """A recommended player to sell."""

    player: MarketPlayer
    score: PlayerScore
    is_protected: bool
    protection_reason: str | None  # "Only GK", "Min DEF", etc.
    budget_recovery: int  # How much selling recovers


@dataclass
class TradePair:
    """A sell->buy swap recommendation."""

    buy_player: MarketPlayer
    sell_player: MarketPlayer
    buy_score: PlayerScore
    sell_score: PlayerScore

    @property
    def net_cost(self) -> int:
        """Cost of the trade (buy price - sell market value)."""
        return self.buy_score.current_price - self.sell_score.market_value

    @property
    def ep_gain(self) -> float:
        """Expected points improvement from this trade."""
        return self.buy_score.expected_points - self.sell_score.expected_points
```

```python
# tests/test_scoring/__init__.py
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scoring/test_models.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add rehoboam/scoring/ tests/test_scoring/
git commit -m "feat(scoring): add data models for EP-first pipeline"
```

______________________________________________________________________

### Task 2: Scorer — utility functions

**Files:**

- Create: `rehoboam/scoring/scorer.py`

- Create: `tests/test_scoring/test_scorer.py`

- [ ] **Step 1: Write tests for consistency extraction**

```python
# tests/test_scoring/test_scorer.py
"""Tests for the pure scoring function."""

from rehoboam.scoring.scorer import (
    extract_games_and_consistency,
    extract_minutes_analysis,
    grade_data_quality,
    score_player,
)
from rehoboam.scoring.models import DataQuality, PlayerData, PlayerScore
from rehoboam.kickbase_client import MarketPlayer
from rehoboam.matchup_analyzer import DoubleGameweekInfo, TeamStrength


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


def _make_performance(
    match_points: list[int], match_minutes: list[int] | None = None
) -> dict:
    """Build a performance dict matching Kickbase API structure."""
    matches = []
    for i, pts in enumerate(match_points):
        match = {"p": pts}
        if match_minutes and i < len(match_minutes):
            match["t"] = match_minutes[i]
        matches.append(match)
    return {"it": [{"ti": "2025", "ph": matches}]}


class TestExtractGamesAndConsistency:
    def test_no_data(self):
        games, consistency = extract_games_and_consistency(None)
        assert games == 0
        assert consistency == 0.0

    def test_empty_seasons(self):
        games, consistency = extract_games_and_consistency({"it": []})
        assert games == 0

    def test_single_game(self):
        perf = _make_performance([80])
        games, consistency = extract_games_and_consistency(perf)
        assert games == 1
        assert consistency == 0.5  # Medium confidence for 1 game

    def test_consistent_player(self):
        # All scores around 60 — very consistent
        perf = _make_performance([58, 62, 60, 61, 59, 60, 62, 58, 60, 61])
        games, consistency = extract_games_and_consistency(perf)
        assert games == 10
        assert consistency > 0.8

    def test_inconsistent_player(self):
        # Wild swings — very inconsistent
        perf = _make_performance([10, 120, 5, 150, 0, 200, 15, 100])
        games, consistency = extract_games_and_consistency(perf)
        assert games == 8
        assert consistency < 0.4

    def test_skips_zero_point_zero_minute_matches(self):
        # Matches where player didn't play (0 points, 0 minutes)
        perf = _make_performance([80, 0, 60, 0, 70], [90, 0, 85, 0, 88])
        games, consistency = extract_games_and_consistency(perf)
        assert games == 3  # Only 3 matches where player actually played


class TestExtractMinutesAnalysis:
    def test_no_data(self):
        trend, avg, is_sub = extract_minutes_analysis(None)
        assert trend is None

    def test_increasing_minutes(self):
        # First half: ~45 min, second half: ~85 min
        perf = _make_performance(
            [50] * 8,
            [40, 45, 50, 42, 80, 85, 90, 88],
        )
        trend, avg, is_sub = extract_minutes_analysis(perf)
        assert trend == "increasing"

    def test_decreasing_minutes(self):
        perf = _make_performance(
            [50] * 8,
            [90, 88, 85, 90, 40, 35, 30, 25],
        )
        trend, avg, is_sub = extract_minutes_analysis(perf)
        assert trend == "decreasing"

    def test_substitute_pattern(self):
        perf = _make_performance([30] * 6, [25, 30, 20, 28, 22, 30])
        trend, avg, is_sub = extract_minutes_analysis(perf)
        assert is_sub is True
        assert avg < 60


class TestGradeDataQuality:
    def test_grade_a(self):
        dq = grade_data_quality(
            games_played=12,
            consistency=0.8,
            has_fixture_data=True,
            has_lineup_data=True,
        )
        assert dq.grade == "A"
        assert dq.warnings == []

    def test_grade_b_few_games(self):
        dq = grade_data_quality(
            games_played=7,
            consistency=0.6,
            has_fixture_data=True,
            has_lineup_data=False,
        )
        assert dq.grade == "B"

    def test_grade_c_sparse(self):
        dq = grade_data_quality(
            games_played=3,
            consistency=0.5,
            has_fixture_data=False,
            has_lineup_data=False,
        )
        assert dq.grade == "C"
        assert any("3 games" in w for w in dq.warnings)

    def test_grade_f_no_games(self):
        dq = grade_data_quality(
            games_played=0,
            consistency=0.0,
            has_fixture_data=False,
            has_lineup_data=False,
        )
        assert dq.grade == "F"
        assert any("No games" in w or "0" in w for w in dq.warnings)

    def test_grade_f_one_game(self):
        dq = grade_data_quality(
            games_played=1,
            consistency=0.5,
            has_fixture_data=True,
            has_lineup_data=True,
        )
        assert dq.grade == "F"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scoring/test_scorer.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement utility functions and grade_data_quality**

```python
# rehoboam/scoring/scorer.py
"""Pure scoring function for the EP-first pipeline.

score_player(PlayerData) -> PlayerScore — no API calls, no side effects.
"""

from .models import DataQuality, PlayerData, PlayerScore


def extract_games_and_consistency(
    performance_data: dict | None,
) -> tuple[int, float]:
    """Extract games played and consistency score from performance data.

    Returns:
        (games_played, consistency_score 0-1 where 1 = very consistent)
    """
    if not performance_data:
        return 0, 0.0

    try:
        seasons = performance_data.get("it", [])
        if not seasons:
            return 0, 0.0

        seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
        current_season = seasons_sorted[0]
        matches = current_season.get("ph", [])

        # Only count matches where player actually played
        matches_played = [m for m in matches if m.get("p", 0) != 0 or m.get("t", 0) > 0]
        games_played = len(matches_played)

        if games_played == 0:
            return 0, 0.0
        if games_played == 1:
            return 1, 0.5

        match_points = [m.get("p", 0) for m in matches_played]
        mean_points = sum(match_points) / games_played
        if mean_points == 0:
            return games_played, 0.0

        variance = sum((p - mean_points) ** 2 for p in match_points) / games_played
        std_dev = variance**0.5
        cv = std_dev / mean_points if mean_points > 0 else 1.0

        # CV 0 = perfect consistency (1.0), CV 2+ = very inconsistent (0.0)
        consistency = max(0.0, 1.0 - (cv / 2.0))
        return games_played, consistency

    except Exception:
        return 0, 0.0


def extract_minutes_analysis(
    performance_data: dict | None,
) -> tuple[str | None, float | None, bool | None]:
    """Extract minutes trend, average minutes, and substitution pattern.

    Returns:
        (minutes_trend, avg_minutes, is_substitute_pattern)
    """
    if not performance_data:
        return None, None, None

    try:
        seasons = performance_data.get("it", [])
        if not seasons:
            return None, None, None

        seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
        current_season = seasons_sorted[0]
        matches = current_season.get("ph", [])

        minutes_data = [m.get("t") for m in matches if m.get("t") is not None]
        if len(minutes_data) < 2:
            return None, None, None

        avg_minutes = sum(minutes_data) / len(minutes_data)

        # Trend: compare first half vs second half
        minutes_trend = "stable"
        if len(minutes_data) >= 4:
            half = len(minutes_data) // 2
            first_avg = sum(minutes_data[:half]) / half
            second_avg = sum(minutes_data[half:]) / (len(minutes_data) - half)
            diff_pct = ((second_avg - first_avg) / max(first_avg, 1)) * 100

            if diff_pct > 15:
                minutes_trend = "increasing"
            elif diff_pct < -15:
                minutes_trend = "decreasing"

        # Substitute pattern
        is_sub = avg_minutes < 60
        if not is_sub and len(minutes_data) >= 3:
            variance = sum((m - avg_minutes) ** 2 for m in minutes_data) / len(
                minutes_data
            )
            if variance**0.5 > 25 and avg_minutes < 75:
                is_sub = True

        return minutes_trend, avg_minutes, is_sub

    except Exception:
        return None, None, None


def grade_data_quality(
    games_played: int,
    consistency: float,
    has_fixture_data: bool,
    has_lineup_data: bool,
) -> DataQuality:
    """Assign a data quality grade based on available data."""
    warnings = []

    if games_played == 0:
        warnings.append("No games played")
    elif games_played <= 1:
        warnings.append(f"Only {games_played} game played")
    elif games_played <= 4:
        warnings.append(f"Only {games_played} games played")

    if not has_fixture_data:
        warnings.append("No fixture data")
    if not has_lineup_data:
        warnings.append("No lineup data")

    # Grade assignment
    if games_played <= 1:
        grade = "F"
    elif games_played <= 4 or (not has_fixture_data and not has_lineup_data):
        grade = "C"
    elif games_played <= 9 or (has_fixture_data != has_lineup_data):
        # 5-9 games, or has only one of fixture/lineup
        grade = "B"
    else:
        # 10+ games with both fixture and lineup data
        grade = "A"

    return DataQuality(
        grade=grade,
        games_played=games_played,
        consistency=consistency,
        has_fixture_data=has_fixture_data,
        has_lineup_data=has_lineup_data,
        warnings=warnings,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scoring/test_scorer.py -v -k "not TestScorePlayer"`
Expected: All utility + grading tests PASS

- [ ] **Step 5: Commit**

```bash
git add rehoboam/scoring/scorer.py tests/test_scoring/test_scorer.py
git commit -m "feat(scoring): add utility functions and data quality grading"
```

______________________________________________________________________

### Task 3: Scorer — main `score_player` function

**Files:**

- Modify: `rehoboam/scoring/scorer.py`

- Modify: `tests/test_scoring/test_scorer.py`

- [ ] **Step 1: Write tests for score_player**

Append to `tests/test_scoring/test_scorer.py`:

```python
def _make_player_data(
    avg_points: float = 12.0,
    points: int = 100,
    performance: dict | None = None,
    player_details: dict | None = None,
    team_strength: TeamStrength | None = None,
    opponent_strength: TeamStrength | None = None,
    is_dgw: bool = False,
    status: int = 0,
) -> PlayerData:
    player = _make_player(average_points=avg_points, points=points, status=status)
    return PlayerData(
        player=player,
        performance=performance,
        player_details=player_details,
        team_strength=team_strength,
        opponent_strength=opponent_strength,
        dgw_info=DoubleGameweekInfo(is_dgw=is_dgw, match_count=2 if is_dgw else 1),
        missing=[],
    )


class TestScorePlayer:
    def test_basic_scoring(self):
        """Average player with no extra data scores based on avg_points only."""
        pd = _make_player_data(avg_points=15.0, points=100)
        ps = score_player(pd)
        assert isinstance(ps, PlayerScore)
        assert ps.base_points == 30.0  # min(15 * 2, 40)
        assert ps.expected_points > 0

    def test_high_avg_caps_at_40(self):
        pd = _make_player_data(avg_points=25.0, points=200)
        ps = score_player(pd)
        assert ps.base_points == 40.0

    def test_zero_avg_points(self):
        pd = _make_player_data(avg_points=0.0, points=0)
        ps = score_player(pd)
        assert ps.expected_points >= 0  # Never negative after clamp

    def test_starter_gets_lineup_bonus(self):
        pd = _make_player_data(avg_points=15.0, player_details={"prob": 1})
        ps = score_player(pd)
        assert ps.lineup_bonus == 20.0

    def test_unlikely_player_gets_penalty(self):
        pd = _make_player_data(avg_points=15.0, player_details={"prob": 4})
        ps = score_player(pd)
        assert ps.lineup_bonus == -20.0

    def test_dgw_multiplier(self):
        pd_normal = _make_player_data(avg_points=15.0, is_dgw=False)
        pd_dgw = _make_player_data(avg_points=15.0, is_dgw=True)
        ps_normal = score_player(pd_normal)
        ps_dgw = score_player(pd_dgw)
        assert ps_dgw.dgw_multiplier == 1.8
        assert ps_dgw.expected_points > ps_normal.expected_points
        if ps_normal.expected_points > 0:
            ratio = ps_dgw.expected_points / ps_normal.expected_points
            assert 1.7 <= ratio <= 1.81

    def test_dgw_note_added(self):
        pd = _make_player_data(avg_points=15.0, is_dgw=True)
        ps = score_player(pd)
        assert "DOUBLE GAMEWEEK" in ps.notes

    def test_scale_goes_above_100(self):
        """Strong DGW player should score above 100 (scale is 0-180)."""
        pd = _make_player_data(
            avg_points=20.0,
            points=200,
            player_details={"prob": 1},
            is_dgw=True,
        )
        ps = score_player(pd)
        # base 40 + lineup 20 + form 10 = 70 min, * 1.8 = 126
        assert ps.expected_points > 100

    def test_clamped_at_180(self):
        """Score should never exceed 180."""
        pd = _make_player_data(
            avg_points=30.0,
            points=500,
            player_details={"prob": 1},
            is_dgw=True,
        )
        perf = _make_performance(
            [100] * 15,  # Very consistent high scorer
            [90] * 15,  # Always plays full game
        )
        pd.performance = perf
        ps = score_player(pd)
        assert ps.expected_points <= 180

    def test_clamped_at_0(self):
        """Score should never go below 0."""
        pd = _make_player_data(
            avg_points=0.0,
            points=0,
            player_details={"prob": 5},
        )
        ps = score_player(pd)
        assert ps.expected_points >= 0

    def test_grade_f_halves_score(self):
        """Grade F players get their EP halved."""
        pd_few = _make_player_data(avg_points=20.0, points=100)
        pd_few.performance = _make_performance([100])  # 1 game
        ps = score_player(pd_few)
        assert ps.data_quality.grade == "F"
        # Base would be 40, but halved due to grade F
        # Exact value depends on other components but should be noticeably lower

    def test_form_hot_streak(self):
        # Recent 5 matches average 25 vs season avg 10 = 2.5x ratio
        perf = _make_performance([5, 5, 5, 5, 5, 25, 30, 20, 25, 25])
        pd = _make_player_data(avg_points=10.0, points=150, performance=perf)
        ps = score_player(pd)
        assert ps.form_bonus == 10.0
        assert "Hot streak" in ps.notes

    def test_form_not_scoring(self):
        # Recent matches all zeros
        perf = _make_performance([50, 60, 40, 0, 0, 0, 0, 0])
        pd = _make_player_data(avg_points=15.0, points=150, performance=perf)
        ps = score_player(pd)
        assert ps.form_bonus == -10.0
        assert "Not scoring" in ps.notes

    def test_price_context_preserved(self):
        pd = _make_player_data(avg_points=15.0)
        ps = score_player(pd)
        assert ps.current_price == 5_000_000
        assert ps.market_value == 5_000_000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scoring/test_scorer.py::TestScorePlayer -v`
Expected: FAIL with `ImportError: cannot import name 'score_player'`

- [ ] **Step 3: Implement score_player**

Append to `rehoboam/scoring/scorer.py`:

```python
def _extract_recent_avg(performance_data: dict | None, last_n: int = 5) -> float | None:
    """Extract average points from the last N matches where the player played."""
    if not performance_data:
        return None
    try:
        seasons = performance_data.get("it", [])
        if not seasons:
            return None
        seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
        matches = seasons_sorted[0].get("ph", [])
        played = [m for m in matches if m.get("p", 0) != 0 or m.get("t", 0) > 0]
        if not played:
            return None
        recent = played[-last_n:]  # Last N matches
        return sum(m.get("p", 0) for m in recent) / len(recent)
    except Exception:
        return None


def score_player(data: PlayerData) -> PlayerScore:
    """Score a player based on expected matchday points.

    Pure function — no API calls, no side effects.
    Scale is 0-180 (not 0-100) to preserve DGW advantage.
    """
    player = data.player
    avg_points = player.average_points
    current_points = player.points
    notes: list[str] = []

    # 1. Base points (0-40) — PRIMARY DRIVER
    base_points = min(avg_points * 2, 40)

    # 2. Consistency bonus (-5 to +15)
    games_played, consistency = extract_games_and_consistency(data.performance)

    consistency_bonus = 0.0
    if data.performance:
        if consistency >= 0.7:
            consistency_bonus = 15.0
            notes.append("Very consistent")
        elif consistency >= 0.3:
            consistency_bonus = consistency * 15
        else:
            consistency_bonus = -5.0
            notes.append("Inconsistent")

    # 3. Lineup probability (-20 to +20)
    lineup_bonus = 0.0
    has_lineup_data = False
    if data.player_details:
        has_lineup_data = True
        prob = data.player_details.get("prob", 5)
        if prob == 1:
            lineup_bonus = 20.0
            notes.append("Starter")
        elif prob == 2:
            lineup_bonus = 10.0
            notes.append("Rotation")
        elif prob == 3:
            lineup_bonus = 0.0
            notes.append("Bench")
        elif prob >= 4:
            lineup_bonus = -20.0
            notes.append("Unlikely to play")

    # 4. Fixture bonus (-10 to +15)
    fixture_bonus = 0.0
    has_fixture_data = data.team_strength is not None
    next_opponent = None

    if data.team_strength and data.opponent_strength:
        # Difficulty based on relative strength
        strength_diff = (
            data.opponent_strength.strength_score - data.team_strength.strength_score
        )
        raw_bonus = -strength_diff / 5  # Scale: 50pt diff -> ±10 bonus
        fixture_bonus = max(-10.0, min(15.0, raw_bonus))
        next_opponent = data.opponent_strength.team_name

        if fixture_bonus >= 5:
            notes.append("Easy fixture")
        elif fixture_bonus <= -5:
            notes.append("Hard fixture")

    # 5. Form bonus (-10 to +10) — recent matches vs season average
    form_bonus = 0.0
    recent_avg = _extract_recent_avg(data.performance, last_n=5)
    if recent_avg is not None and avg_points > 0:
        form_ratio = recent_avg / avg_points
        if form_ratio > 2.0:
            form_bonus = 10.0
            notes.append("Hot streak")
        elif form_ratio > 1.3:
            form_bonus = 5.0
        elif form_ratio < 0.5 and recent_avg > 0:
            form_bonus = -5.0
            notes.append("Below average")
        elif recent_avg == 0:
            form_bonus = -10.0
            notes.append("Not scoring")
    elif avg_points == 0:
        form_bonus = -10.0
        notes.append("Not scoring")

    # 6. Minutes bonus (-10 to +10)
    minutes_bonus = 0.0
    if data.performance:
        trend, avg_min, is_sub = extract_minutes_analysis(data.performance)
        if trend == "increasing":
            minutes_bonus = 10.0
            notes.append("Minutes increasing")
        elif trend == "decreasing":
            minutes_bonus = -10.0
            notes.append("Minutes decreasing")
        elif trend == "stable" and avg_min is not None and avg_min < 30:
            minutes_bonus = -8.0
            notes.append("Rarely plays")

    # Sum components
    raw_total = (
        base_points
        + consistency_bonus
        + lineup_bonus
        + fixture_bonus
        + form_bonus
        + minutes_bonus
    )

    # DGW multiplier
    dgw_multiplier = 1.8 if data.dgw_info.is_dgw else 1.0
    if data.dgw_info.is_dgw:
        notes.append("DOUBLE GAMEWEEK")

    total = raw_total * dgw_multiplier

    # Data quality grading
    data_quality = grade_data_quality(
        games_played=games_played,
        consistency=consistency,
        has_fixture_data=has_fixture_data,
        has_lineup_data=has_lineup_data,
    )

    # Grade F penalty: halve the score
    if data_quality.grade == "F":
        total *= 0.5

    # Clamp to 0-180
    total = max(0, min(180, total))

    price = getattr(player, "price", player.market_value)

    return PlayerScore(
        player_id=player.id,
        expected_points=round(total, 1),
        data_quality=data_quality,
        base_points=base_points,
        consistency_bonus=round(consistency_bonus, 1),
        lineup_bonus=lineup_bonus,
        fixture_bonus=round(fixture_bonus, 1),
        form_bonus=form_bonus,
        minutes_bonus=minutes_bonus,
        dgw_multiplier=dgw_multiplier,
        is_dgw=data.dgw_info.is_dgw,
        next_opponent=next_opponent,
        notes=notes,
        current_price=price,
        market_value=player.market_value,
        position=player.position,
        average_points=avg_points,
        status=player.status,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scoring/test_scorer.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add rehoboam/scoring/scorer.py tests/test_scoring/test_scorer.py
git commit -m "feat(scoring): implement score_player pure function"
```

______________________________________________________________________

## Chunk 2: Collector and DecisionEngine

### Task 4: DataCollector

**Files:**

- Create: `rehoboam/scoring/collector.py`

- Create: `tests/test_scoring/test_collector.py`

- [ ] **Step 1: Write tests for DataCollector**

```python
# tests/test_scoring/test_collector.py
"""Tests for DataCollector — assembles PlayerData from pre-fetched API data."""

from rehoboam.scoring.collector import DataCollector
from rehoboam.scoring.models import PlayerData
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
    def setup_method(self):
        self.collector = DataCollector(matchup_analyzer=MatchupAnalyzer())

    def test_collect_with_all_data(self):
        player = _make_player()
        performance = {"it": [{"ti": "2025", "ph": [{"p": 80, "t": 90}]}]}
        player_details = {"prob": 1, "st": 0, "tid": "t1", "mdsum": []}
        team_profiles = {
            "t1": {"tid": "t1", "tn": "Test FC", "pl": 5, "tw": 10, "td": 3, "tl": 5},
        }

        pd = self.collector.collect(player, performance, player_details, team_profiles)
        assert isinstance(pd, PlayerData)
        assert pd.player.id == "p1"
        assert pd.performance is not None
        assert pd.player_details is not None
        assert pd.team_strength is not None
        assert pd.missing == []

    def test_collect_missing_performance(self):
        player = _make_player()
        pd = self.collector.collect(player, None, None, {})
        assert "performance" in pd.missing
        assert "player_details" in pd.missing

    def test_collect_detects_dgw(self):
        player = _make_player()
        # Two matches in same matchday = DGW
        player_details = {
            "prob": 1,
            "st": 0,
            "tid": "t1",
            "mdsum": [
                {"mdid": "md1", "mdst": 0, "t1": "t1", "t2": "t2"},
                {"mdid": "md1", "mdst": 0, "t1": "t3", "t2": "t1"},
            ],
        }
        pd = self.collector.collect(player, None, player_details, {})
        assert pd.dgw_info.is_dgw is True

    def test_collect_no_dgw(self):
        player = _make_player()
        player_details = {
            "prob": 1,
            "st": 0,
            "tid": "t1",
            "mdsum": [
                {"mdid": "md1", "mdst": 0, "t1": "t1", "t2": "t2"},
                {"mdid": "md2", "mdst": 0, "t1": "t3", "t2": "t1"},
            ],
        }
        pd = self.collector.collect(player, None, player_details, {})
        assert pd.dgw_info.is_dgw is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scoring/test_collector.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement DataCollector**

```python
# rehoboam/scoring/collector.py
"""DataCollector — assembles PlayerData from pre-fetched API data."""

from ..kickbase_client import MarketPlayer
from ..matchup_analyzer import DoubleGameweekInfo, MatchupAnalyzer
from .models import PlayerData


class DataCollector:
    """Assembles and validates data for scoring. Does NOT call the API."""

    def __init__(self, matchup_analyzer: MatchupAnalyzer):
        self.matchup_analyzer = matchup_analyzer

    def collect(
        self,
        player: MarketPlayer,
        performance: dict | None,
        player_details: dict | None,
        team_profiles: dict[str, dict],
    ) -> PlayerData:
        """Assemble PlayerData from pre-fetched data, flagging what's missing."""
        missing = []

        if performance is None:
            missing.append("performance")
        if player_details is None:
            missing.append("player_details")

        # DGW detection
        dgw_info = DoubleGameweekInfo(is_dgw=False)
        if player_details:
            dgw_info = self.matchup_analyzer.detect_double_gameweek(player_details)

        # Team strength
        team_strength = None
        team_profile = team_profiles.get(player.team_id)
        if team_profile:
            team_strength = self.matchup_analyzer.get_team_strength(team_profile)
        else:
            missing.append("team_strength")

        # Opponent strength (from next matchup)
        opponent_strength = None
        if player_details:
            next_matchup = self.matchup_analyzer.get_next_matchup(player_details)
            if next_matchup:
                opponent_profile = team_profiles.get(next_matchup.opponent_id)
                if opponent_profile:
                    opponent_strength = self.matchup_analyzer.get_team_strength(
                        opponent_profile
                    )
                else:
                    missing.append("opponent_strength")
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
            dgw_info=dgw_info,
            missing=missing,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scoring/test_collector.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add rehoboam/scoring/collector.py tests/test_scoring/test_collector.py
git commit -m "feat(scoring): add DataCollector for assembling player data"
```

______________________________________________________________________

### Task 5: DecisionEngine

**Files:**

- Create: `rehoboam/scoring/decision.py`

- Create: `tests/test_scoring/test_decision.py`

- Modify: `rehoboam/config.py`

- [ ] **Step 1: Write tests for DecisionEngine**

```python
# tests/test_scoring/test_decision.py
"""Tests for DecisionEngine — buy/sell/lineup/trade-pair from PlayerScore."""

from rehoboam.scoring.decision import DecisionEngine
from rehoboam.scoring.models import DataQuality, PlayerScore
from rehoboam.analyzer import RosterContext
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
        "average_points": 20.0,
        "status": 0,
    }
    defaults.update(overrides)
    return MarketPlayer(**defaults)


def _make_score(
    player_id: str = "p1",
    ep: float = 60.0,
    grade: str = "A",
    avg_points: float = 20.0,
    price: int = 5_000_000,
    market_value: int = 5_000_000,
    position: str = "Midfielder",
    status: int = 0,
) -> PlayerScore:
    return PlayerScore(
        player_id=player_id,
        expected_points=ep,
        data_quality=DataQuality(
            grade=grade,
            games_played=15,
            consistency=0.8,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        ),
        base_points=30.0,
        consistency_bonus=10.0,
        lineup_bonus=20.0,
        fixture_bonus=0.0,
        form_bonus=0.0,
        minutes_bonus=0.0,
        dgw_multiplier=1.0,
        is_dgw=False,
        next_opponent=None,
        notes=[],
        current_price=price,
        market_value=market_value,
        position=position,
        average_points=avg_points,
        status=status,
    )


def _make_roster_context(
    position: str, current_count: int, minimum_count: int
) -> RosterContext:
    return RosterContext(
        position=position,
        current_count=current_count,
        minimum_count=minimum_count,
        existing_players=[],
        weakest_player=None,
        is_below_minimum=current_count < minimum_count,
        upgrade_potential=0.0,
    )


class TestRecommendBuys:
    def test_sorts_by_ep_descending(self):
        engine = DecisionEngine()
        scores = [
            _make_score("p1", ep=50.0, avg_points=20.0),
            _make_score("p2", ep=80.0, avg_points=25.0),
            _make_score("p3", ep=65.0, avg_points=22.0),
        ]
        players = {
            s.player_id: _make_player(id=s.player_id, average_points=20.0)
            for s in scores
        }
        recs = engine.recommend_buys(scores, [], {}, 10_000_000, players)
        assert recs[0].score.player_id == "p2"
        assert recs[1].score.player_id == "p3"

    def test_filters_grade_f(self):
        engine = DecisionEngine()
        scores = [
            _make_score("p1", ep=80.0, grade="F", avg_points=20.0),
            _make_score("p2", ep=60.0, grade="A", avg_points=20.0),
        ]
        players = {
            s.player_id: _make_player(id=s.player_id, average_points=20.0)
            for s in scores
        }
        recs = engine.recommend_buys(scores, [], {}, 10_000_000, players)
        assert len(recs) == 1
        assert recs[0].score.player_id == "p2"

    def test_filters_low_avg_points(self):
        engine = DecisionEngine()
        scores = [
            _make_score("p1", ep=60.0, avg_points=15.0),  # Below 20 threshold
        ]
        players = {"p1": _make_player(id="p1", average_points=15.0)}
        recs = engine.recommend_buys(scores, [], {}, 10_000_000, players)
        assert len(recs) == 0

    def test_filters_low_ep(self):
        engine = DecisionEngine(min_expected_points_to_buy=30.0)
        scores = [
            _make_score("p1", ep=20.0, avg_points=20.0),  # Below 30 EP threshold
        ]
        players = {"p1": _make_player(id="p1", average_points=20.0)}
        recs = engine.recommend_buys(scores, [], {}, 10_000_000, players)
        assert len(recs) == 0

    def test_fills_gap_bonus(self):
        engine = DecisionEngine()
        scores = [_make_score("p1", ep=60.0, avg_points=20.0, position="Goalkeeper")]
        players = {
            "p1": _make_player(id="p1", position="Goalkeeper", average_points=20.0)
        }
        roster_ctx = {"Goalkeeper": _make_roster_context("Goalkeeper", 0, 1)}
        recs = engine.recommend_buys(scores, [], roster_ctx, 10_000_000, players)
        assert recs[0].roster_bonus == 10.0
        assert recs[0].reason == "fills_gap"

    def test_respects_budget(self):
        engine = DecisionEngine()
        scores = [_make_score("p1", ep=80.0, price=10_000_000, avg_points=20.0)]
        players = {"p1": _make_player(id="p1", price=10_000_000, average_points=20.0)}
        recs = engine.recommend_buys(scores, [], {}, 5_000_000, players)
        assert len(recs) == 0


class TestRecommendSells:
    def test_sorts_by_ep_ascending(self):
        engine = DecisionEngine()
        scores = [
            _make_score("p1", ep=70.0),
            _make_score("p2", ep=30.0),
            _make_score("p3", ep=50.0),
        ]
        players = {s.player_id: _make_player(id=s.player_id) for s in scores}
        recs = engine.recommend_sells(scores, {}, players)
        assert recs[0].score.player_id == "p2"

    def test_protects_position_minimum(self):
        engine = DecisionEngine()
        scores = [_make_score("p1", ep=20.0)]
        players = {"p1": _make_player(id="p1", position="Goalkeeper")}
        roster_ctx = {"Goalkeeper": _make_roster_context("Goalkeeper", 1, 1)}
        recs = engine.recommend_sells(scores, roster_ctx, players)
        assert recs[0].is_protected is True
        assert recs[0].protection_reason is not None


class TestBuildTradePairs:
    def test_pairs_lowest_sell_with_highest_buy(self):
        engine = DecisionEngine(min_ep_upgrade_threshold=10.0)
        market_scores = [_make_score("m1", ep=80.0, price=8_000_000, avg_points=25.0)]
        squad_scores = [_make_score("s1", ep=30.0, market_value=3_000_000)]
        market_players = {
            "m1": _make_player(id="m1", price=8_000_000, average_points=25.0)
        }
        squad_players = {"s1": _make_player(id="s1", market_value=3_000_000)}
        pairs = engine.build_trade_pairs(
            market_scores,
            squad_scores,
            {},
            10_000_000,
            market_players,
            squad_players,
        )
        assert len(pairs) == 1
        assert pairs[0].ep_gain == 50.0

    def test_no_pair_below_threshold(self):
        engine = DecisionEngine(min_ep_upgrade_threshold=10.0)
        market_scores = [_make_score("m1", ep=35.0, avg_points=20.0)]
        squad_scores = [_make_score("s1", ep=30.0)]
        market_players = {"m1": _make_player(id="m1", average_points=20.0)}
        squad_players = {"s1": _make_player(id="s1")}
        pairs = engine.build_trade_pairs(
            market_scores,
            squad_scores,
            {},
            10_000_000,
            market_players,
            squad_players,
        )
        assert len(pairs) == 0


class TestSelectLineup:
    def test_returns_player_id_to_ep_dict(self):
        engine = DecisionEngine()
        scores = [_make_score(f"p{i}", ep=float(60 + i)) for i in range(15)]
        result = engine.select_lineup(scores)
        assert isinstance(result, dict)
        assert all(isinstance(v, float) for v in result.values())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scoring/test_decision.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add config settings**

Add to `rehoboam/config.py` in the `Settings` class, after the `min_upgrade_value_score_diff` field:

```python
    min_expected_points_to_buy: float = Field(
        default=30.0,
        description="Minimum expected points (0-180 scale) to consider buying a player",
    )
    min_ep_upgrade_threshold: float = Field(
        default=10.0,
        description="Minimum EP gain to consider a trade pair worthwhile",
    )
```

- [ ] **Step 4: Implement DecisionEngine**

```python
# rehoboam/scoring/decision.py
"""DecisionEngine — buy/sell/lineup/trade-pair decisions from PlayerScore."""

from ..analyzer import RosterContext
from ..kickbase_client import MarketPlayer
from .models import BuyRecommendation, PlayerScore, SellRecommendation, TradePair


class DecisionEngine:
    """Makes buy/sell/lineup decisions from PlayerScore lists.

    PlayerScore carries position, average_points, status, current_price,
    and market_value — so the engine does not need separate player lookups.
    """

    def __init__(
        self,
        min_avg_points_to_buy: float = 20.0,
        min_avg_points_emergency: float = 10.0,
        min_expected_points_to_buy: float = 30.0,
        min_ep_upgrade_threshold: float = 10.0,
    ):
        self.min_avg_points_to_buy = min_avg_points_to_buy
        self.min_avg_points_emergency = min_avg_points_emergency
        self.min_expected_points_to_buy = min_expected_points_to_buy
        self.min_ep_upgrade_threshold = min_ep_upgrade_threshold

    def recommend_buys(
        self,
        market_scores: list[PlayerScore],
        squad_scores: list[PlayerScore],
        roster_context: dict[str, RosterContext],
        budget: int,
        market_players: dict[str, MarketPlayer],
        is_emergency: bool = False,
        top_n: int = 5,
    ) -> list[BuyRecommendation]:
        """Recommend players to buy, sorted by effective EP."""
        min_avg = (
            self.min_avg_points_emergency
            if is_emergency
            else self.min_avg_points_to_buy
        )
        recs = []

        for score in market_scores:
            # Quality gates
            if score.data_quality.grade == "F":
                continue
            if score.average_points < min_avg:
                continue
            if score.expected_points < self.min_expected_points_to_buy:
                continue
            if score.current_price > budget:
                continue
            if score.status != 0:
                continue

            player = market_players.get(score.player_id)
            if not player:
                continue

            # Roster bonus
            roster_bonus = 0.0
            reason = "additional"
            ctx = roster_context.get(score.position)
            if ctx and ctx.is_below_minimum:
                roster_bonus = 10.0
                reason = "fills_gap"
            elif ctx and not ctx.is_below_minimum:
                reason = "upgrade"

            recs.append(
                BuyRecommendation(
                    player=player,
                    score=score,
                    roster_bonus=roster_bonus,
                    reason=reason,
                )
            )

        recs.sort(key=lambda r: r.effective_ep, reverse=True)
        return recs[:top_n]

    def recommend_sells(
        self,
        squad_scores: list[PlayerScore],
        roster_context: dict[str, RosterContext],
        squad_players: dict[str, MarketPlayer],
    ) -> list[SellRecommendation]:
        """Recommend players to sell, sorted by lowest EP first."""
        recs = []

        for score in squad_scores:
            player = squad_players.get(score.player_id)
            if not player:
                continue

            # Check position protection
            is_protected = False
            protection_reason = None
            ctx = roster_context.get(score.position)
            if ctx and ctx.current_count <= ctx.minimum_count:
                is_protected = True
                protection_reason = f"Min {score.position}"

            recs.append(
                SellRecommendation(
                    player=player,
                    score=score,
                    is_protected=is_protected,
                    protection_reason=protection_reason,
                    budget_recovery=score.market_value,
                )
            )

        recs.sort(key=lambda r: r.score.expected_points)
        return recs

    def build_trade_pairs(
        self,
        market_scores: list[PlayerScore],
        squad_scores: list[PlayerScore],
        roster_context: dict[str, RosterContext],
        budget: int,
        market_players: dict[str, MarketPlayer],
        squad_players: dict[str, MarketPlayer],
        top_n: int = 5,
    ) -> list[TradePair]:
        """Build sell->buy swap pairs with positive EP gain."""
        # Get unprotected sell candidates sorted by lowest EP
        sells = self.recommend_sells(squad_scores, roster_context, squad_players)
        sellable = [s for s in sells if not s.is_protected]

        # Get buy candidates (relaxed budget — selling frees money)
        buys = self.recommend_buys(
            market_scores,
            squad_scores,
            roster_context,
            budget=budget + max((s.budget_recovery for s in sellable), default=0),
            market_players=market_players,
            top_n=20,
        )

        pairs = []
        used_sells = set()
        used_buys = set()

        for buy_rec in buys:
            for sell_rec in sellable:
                if sell_rec.score.player_id in used_sells:
                    continue
                if buy_rec.score.player_id in used_buys:
                    break

                ep_gain = buy_rec.score.expected_points - sell_rec.score.expected_points
                net_cost = buy_rec.score.current_price - sell_rec.score.market_value

                if ep_gain >= self.min_ep_upgrade_threshold and net_cost <= budget:
                    pairs.append(
                        TradePair(
                            buy_player=buy_rec.player,
                            sell_player=sell_rec.player,
                            buy_score=buy_rec.score,
                            sell_score=sell_rec.score,
                        )
                    )
                    used_sells.add(sell_rec.score.player_id)
                    used_buys.add(buy_rec.score.player_id)
                    break

        return pairs[:top_n]

    def select_lineup(
        self,
        squad_scores: list[PlayerScore],
    ) -> dict[str, float]:
        """Return {player_id: expected_points} for formation.select_best_eleven()."""
        return {s.player_id: s.expected_points for s in squad_scores}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_scoring/test_decision.py -v`
Expected: All tests PASS

- [ ] **Step 6: Update __init__.py exports**

Add to `rehoboam/scoring/__init__.py`:

```python
from .collector import DataCollector
from .decision import DecisionEngine
from .scorer import score_player
```

And update `__all__`:

```python
__all__ = [
    "BuyRecommendation",
    "DataCollector",
    "DataQuality",
    "DecisionEngine",
    "PlayerData",
    "PlayerScore",
    "SellRecommendation",
    "TradePair",
    "score_player",
]
```

- [ ] **Step 7: Run all scoring tests**

Run: `pytest tests/test_scoring/ -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add rehoboam/scoring/ rehoboam/config.py tests/test_scoring/
git commit -m "feat(scoring): add DecisionEngine and DataCollector"
```

______________________________________________________________________

## Chunk 3: Integration

### Task 6: Wire into `compact_display.py`

**Files:**

- Modify: `rehoboam/compact_display.py`

- [ ] **Step 1: Add `display_ep_action_plan` method**

Add a new method to `CompactDisplay` that accepts the new types. Keep the existing `display_action_plan` method untouched for the `trade` command.

The new method should:

- Accept `buy_recs: list[BuyRecommendation]`, `sell_recs: list[SellRecommendation]`, `trade_pairs: list[TradePair]`, `squad_summary: dict`, `lineup_map: dict[str, float]`
- Show data quality badge per player: `[A]`, `[B]`, `[C]⚠`, `[F]⚠⚠`
- Show DGW indicator: `⚡DGW` next to double gameweek players
- Show EP instead of value_score
- Show trade pairs with net cost and EP gain when squad is full

Read `compact_display.py` in full before implementing to match existing style (Rich tables, emoji indicators, formatting conventions).

- [ ] **Step 2: Verify existing tests still pass**

Run: `pytest tests/ -v`
Expected: All existing tests PASS (no regression)

- [ ] **Step 3: Commit**

```bash
git add rehoboam/compact_display.py
git commit -m "feat(display): add EP-based action plan display"
```

______________________________________________________________________

### Task 7: Wire into `trader.py`

**Files:**

- Modify: `rehoboam/trader.py`

- [ ] **Step 1: Add EP pipeline method to Trader**

Add a new method `run_ep_analysis(league)` to `Trader` that:

1. Fetches market players and squad (reuses existing fetch logic)
1. For each player, fetches performance, details, team profiles (reuses existing per-player fetch loop)
1. Passes pre-fetched data to `DataCollector.collect()`
1. Calls `score_player()` on each `PlayerData`
1. Passes scores to `DecisionEngine` for buy/sell/trade-pair decisions
1. Calls `compact_display.display_ep_action_plan()` to render output

Read `trader.py` in full before implementing. The new method should reuse the existing data-fetching patterns (especially `_get_matchup_context`, `trend_service`, `history_cache`) but route through the new scoring pipeline instead of `MarketAnalyzer`.

- [ ] **Step 2: Verify existing tests still pass**

Run: `pytest tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 3: Commit**

```bash
git add rehoboam/trader.py
git commit -m "feat(trader): add EP pipeline analysis method"
```

______________________________________________________________________

### Task 8: Wire into `cli.py`

**Files:**

- Modify: `rehoboam/cli.py`

- [ ] **Step 1: Update `analyze` command to use EP pipeline**

Change the `analyze` command to call `trader.run_ep_analysis(league)` instead of the current `display_compact_action_plan()` flow. The `--detailed` flag can remain for the old flow if needed as a fallback.

Read `cli.py` in full before implementing to understand the command structure.

- [ ] **Step 2: Update `lineup` command to use EP pipeline**

The `lineup` command should use `DataCollector` -> `score_player()` -> `DecisionEngine.select_lineup()` -> `select_best_eleven()`.

- [ ] **Step 3: Manual smoke test**

Run: `rehoboam analyze --help`
Expected: Command shows help without errors

Run: `rehoboam login`
Expected: Successful login (requires real credentials in `.env`)

- [ ] **Step 4: Commit**

```bash
git add rehoboam/cli.py
git commit -m "feat(cli): wire analyze and lineup to EP pipeline"
```

______________________________________________________________________

### Task 9: Final verification

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Run linting**

Run: `ruff check rehoboam/ --fix && black rehoboam/`
Expected: Clean output

- [ ] **Step 3: Run type checking**

Run: `mypy rehoboam/scoring/ --ignore-missing-imports`
Expected: No errors

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "chore: fix lint/type issues in scoring pipeline"
```
