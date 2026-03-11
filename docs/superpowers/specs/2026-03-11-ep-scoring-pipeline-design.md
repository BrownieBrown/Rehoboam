# EP-First Scoring Pipeline

**Date:** 2026-03-11
**Status:** Approved
**Goal:** Replace market-value-driven `value_score` with a unified expected-points pipeline for all matchday decisions.

## Problem

The bot's `analyze` command and buy/sell decisions are driven by `value_score`, which mixes market-value signals (trend, discount, momentum) with points signals. This optimizes for portfolio appreciation, not matchday points — which is what actually wins the league.

Additionally:

- **Data quality is invisible.** A player with 1 game and 100 points looks identical to a 15-game player averaging 100. Silent bad data degrades recommendations.
- **DGW awareness is missing from decisions.** The EP calculator supports a `is_dgw` flag but nothing detects or wires it.
- **Expected points exist but are siloed.** `expected_points.py` only powers the `lineup` command, not buy/sell.

## Design

### Core Data Model

```python
@dataclass
class DataQuality:
    grade: str  # "A", "B", "C", "F"
    games_played: int
    consistency: float  # 0-1
    has_fixture_data: bool
    has_lineup_data: bool
    warnings: list[str]  # ["Only 2 games played", "No fixture data"]


@dataclass
class PlayerScore:
    player_id: str
    expected_points: float  # The ONE number driving all decisions (0-180 scale)
    data_quality: DataQuality

    # Components (for transparency/display)
    base_points: float  # From avg_points, capped at 40
    consistency_bonus: float  # -5 to +15
    lineup_bonus: float  # -20 to +20
    fixture_bonus: float  # -10 to +15
    form_bonus: float  # -10 to +10
    minutes_bonus: float  # -10 to +10
    dgw_multiplier: float  # 1.0 normally, 1.8 for DGW

    # Context
    is_dgw: bool
    next_opponent: str | None
    notes: list[str]

    # Price context (needed by display and budget calculations)
    current_price: int
    market_value: int
```

### Three-Layer Pipeline

```
API Data -> [DataCollector] -> [Scorer] -> [DecisionEngine] -> Display/Trade
```

**1. DataCollector** (`scoring/collector.py`)

Gathers raw data for a player and flags what's missing. Returns `PlayerData`:

```python
@dataclass
class PlayerData:
    player: MarketPlayer
    performance: dict | None
    player_details: dict | None
    team_strength: TeamStrength | None
    opponent_strength: TeamStrength | None
    dgw_info: DoubleGameweekInfo
    missing: list[str]  # ["performance", "opponent_strength", ...]
```

**Data fetching strategy:** `DataCollector` does NOT call the API directly. It receives pre-fetched data from the caller (typically `Trader`), which already batch-fetches market players, then per-player details, performance, and team profiles. Team strength data is cached via `MatchupAnalyzer.team_cache`. This preserves the existing fetch pattern and avoids new rate-limiting concerns.

The collector's job is to **assemble and validate** data, not fetch it:

```python
class DataCollector:
    def __init__(self, matchup_analyzer: MatchupAnalyzer):
        self.matchup_analyzer = matchup_analyzer

    def collect(
        self,
        player: MarketPlayer,
        performance: dict | None,
        player_details: dict | None,
        team_profiles: dict[str, dict],  # Pre-fetched team profiles by team_id
    ) -> PlayerData: ...
```

**2. Scorer** (`scoring/scorer.py`)

Pure function: `score_player(PlayerData) -> PlayerScore`. No API calls, no side effects.

**Scorer owns its own utility functions** for extracting consistency, minutes trends, etc. These are reimplemented from `PlayerValue._extract_games_and_consistency()` and `_extract_minutes_analysis()` — not imported from `value_calculator.py`. This avoids a fragile cross-dependency between the new and deprecated pipelines.

Scoring formula:

| Component   | Range      | Formula                                                                                           |
| ----------- | ---------- | ------------------------------------------------------------------------------------------------- |
| Base points | 0-40       | `min(avg_points * 2, 40)`                                                                         |
| Consistency | -5 to +15  | Consistency score 0-1 (from match-by-match CV). >= 0.7: `+15`, >= 0.3: `score * 15`, \< 0.3: `-5` |
| Lineup      | -20 to +20 | prob=1: `+20`, prob=2: `+10`, prob=3: `0`, prob>=4: `-20`                                         |
| Fixture     | -10 to +15 | SOS bonus_points from matchup analyzer, clamped to \[-10, +15\]                                   |
| Form        | -10 to +10 | `current_points / avg_points` ratio. > 2.0: `+10`, > 1.3: `+5`, \< 0.5: `-5`, = 0: `-10`          |
| Minutes     | -10 to +10 | Trend from half-season comparison. Increasing: `+10`, decreasing: `-10`, stable \< 30min: `-8`    |
| DGW         | x1.8       | Multiplier applied at the end, **before clamping to 0-180**                                       |

**Scale is 0-180** (not 0-100). This preserves DGW advantage for strong players: a player with base EP 70 * 1.8 = 126 is correctly distinguishable from a non-DGW player at 70. The theoretical max (100 base * 1.8) = 180.

Data quality grading:

- **A**: 10+ games, has fixture + lineup data
- **B**: 5-9 games, has at least one of fixture/lineup
- **C**: 2-4 games, or missing both fixture and lineup
- **F**: 0-1 games — expected_points halved as confidence penalty

**3. DecisionEngine** (`scoring/decision.py`)

Consumes `PlayerScore` for all matchday decisions. Also takes roster context for position-aware decisions.

```python
class DecisionEngine:
    def recommend_buys(
        self,
        market_scores: list[PlayerScore],
        squad_scores: list[PlayerScore],
        roster_context: dict[str, RosterContext],
        budget: int,
    ) -> list[BuyRecommendation]: ...

    def recommend_sells(
        self,
        squad_scores: list[PlayerScore],
        roster_context: dict[str, RosterContext],
    ) -> list[SellRecommendation]: ...

    def build_trade_pairs(
        self,
        market_scores: list[PlayerScore],
        squad_scores: list[PlayerScore],
        roster_context: dict[str, RosterContext],
        budget: int,
    ) -> list[TradePair]: ...

    def select_lineup(
        self,
        squad_scores: list[PlayerScore],
    ) -> dict[str, float]:
        # Returns {player_id: expected_points} for formation.select_best_eleven()
        ...
```

**Roster-aware decisions:**

- `RosterContext` (from existing `analyzer.py`) is consumed by `DecisionEngine`, not embedded in `PlayerScore`. `PlayerScore` stays pure (just about the player), while `DecisionEngine` applies roster logic on top.
- Below position minimum: Buy gets +10 EP bonus for sorting (same as current `fills_gap` behavior)
- At/above minimum: Buy is compared to weakest squad player at that position; only shown if EP gain > `min_ep_upgrade_threshold`
- Sell: Players at position minimum are protected (cannot sell last GK, can't go below 3 DEF, etc.)

**Quality gates for buy recommendations:**

- Data quality >= C (grade F never recommended)
- `avg_points >= 20` (existing quality gate, retained)
- `avg_points >= 10` for emergency fills when squad \< `min_squad_size`
- Grade F players are never auto-traded, only shown in verbose/manual review

**Trade pair threshold:** EP gain must exceed `min_ep_upgrade_threshold` (default: 10.0, configurable in `config.py`). This replaces the current `MIN_UPGRADE_THRESHOLD` which operates on value_score.

## File Layout

### New files

```
rehoboam/scoring/
    __init__.py
    models.py       # PlayerScore, DataQuality, PlayerData, BuyRecommendation, SellRecommendation, TradePair
    collector.py    # DataCollector
    scorer.py       # score_player() pure function + utility functions (consistency, minutes extraction)
    decision.py     # DecisionEngine
```

### Changed files

| File                 | Change                                                                                                                                                                                                                                                   |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `trader.py`          | `analyze_market()` uses DataCollector -> Scorer -> DecisionEngine instead of MarketAnalyzer. Pre-fetched data passed to DataCollector.                                                                                                                   |
| `compact_display.py` | New `display_ep_action_plan()` method accepting `BuyRecommendation`, `SellRecommendation`, `TradePair` lists with `PlayerScore` inside. Shows data quality badge (A/B/C/F) and DGW indicator. Existing `display_action_plan()` kept for `trade` command. |
| `cli.py`             | `analyze` and `lineup` wire up new pipeline                                                                                                                                                                                                              |
| `config.py`          | Add `min_expected_points_to_buy: float = 30.0` and `min_ep_upgrade_threshold: float = 10.0` settings                                                                                                                                                     |

### Unchanged files

- `api.py`, `kickbase_client.py` — API layer
- `bidding_strategy.py`, `bid_learner.py` — Bidding logic
- `matchup_analyzer.py` — Called by DataCollector, no changes needed

### Minor changes

- `formation.py` — No signature changes. Callers pass `{ps.player_id: ps.expected_points for ps in scores}` to `select_best_eleven()`.

### Deprecated (kept for trade command)

- `value_calculator.py` — Still used by `trade` command for profit trading
- `expected_points.py` — Logic absorbed into `scorer.py`
- `analyzer.py` `MarketAnalyzer` — Only used by `trade` command
- `squad_optimizer.py` — Functionality absorbed into `DecisionEngine`. Kept for `trade` command if used there.

## Migration Path

Dual-path coexistence — no big bang:

```
analyze command -> DataCollector -> Scorer -> DecisionEngine -> CompactDisplay (new method)
trade command   -> MarketAnalyzer -> PlayerValue (unchanged)
lineup command  -> DataCollector -> Scorer -> formation.select_best_eleven
```

The `trade` command keeps using `value_score` for profit-trading. Once EP results are validated against real matchdays, `value_score` can be gradually sunset.

## Learning Systems

**Out of scope for this phase.** The learning systems (`factor_weight_learner`, `historical_tracker`, `bid_learner`) currently optimize `value_score` weights. They continue operating on the `trade` command path unchanged.

Future work: adapt `historical_tracker` to record `PlayerScore`-based recommendations and track EP prediction accuracy. This requires matchday result data to compare predictions against actuals — a separate project.

## Data Quality Strategy

Every recommendation shows its data quality grade. The display layer uses visual indicators:

- **A**: Full confidence, no caveats
- **B**: Minor gaps, still actionable
- **C**: Sparse data, shown with warning
- **F**: Insufficient data, score is halved and marked as unreliable

Players with grade F are never auto-traded (only shown in manual review).

## Testing Strategy

The pure-function scorer is the most testable part:

- Unit tests with synthetic `PlayerData` covering every component and formula
- Edge cases: 0 games played, missing fixture data, DGW multiplier at scale ceiling, injured player
- Data quality grading: verify grade assignment for each threshold boundary
- Roster-aware decisions: position minimum enforcement, fills_gap bonus, upgrade threshold
- Integration tests: full pipeline from mock API data to buy/sell recommendations
- Regression: compare new pipeline recommendations against historical value_score decisions
