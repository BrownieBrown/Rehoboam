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
    base_points: float
    consistency_bonus: float
    lineup_bonus: float
    fixture_bonus: float
    form_bonus: float
    minutes_bonus: float
    dgw_multiplier: float  # 1.0 normally, 1.8 for DGW
    is_dgw: bool
    next_opponent: str | None
    notes: list[str]
    current_price: int
    market_value: int
    average_points: float = 0.0
    position: str = ""
    lineup_probability: int | None = None  # 1=starter, 2-3=rotation, 4-5=unlikely
    minutes_trend: str | None = None  # "increasing" | "decreasing" | "stable"


@dataclass
class PlayerData:
    """Raw data assembled by DataCollector for a single player."""

    player: MarketPlayer
    performance: dict | None
    player_details: dict | None
    team_strength: TeamStrength | None
    opponent_strength: TeamStrength | None
    is_dgw: bool
    missing: list[str] = field(default_factory=list)
    upcoming_opponent_strengths: list[TeamStrength] = field(default_factory=list)


@dataclass
class BuyRecommendation:
    """EP-based buy recommendation."""

    score: PlayerScore
    player: MarketPlayer
    marginal_ep_gain: float
    effective_ep: float  # Expected points used for ranking
    replaces_player_id: str | None
    replaces_player_name: str | None
    roster_impact: str  # "fills_gap", "upgrade", "additional"
    roster_bonus: float  # Numeric bonus for bidding strategy
    reason: str
    recommended_bid: int | None = None
    sell_plan: "SellPlan | None" = None  # Paired sell plan when buy exceeds budget
    metadata: dict | None = None


@dataclass
class SellRecommendation:
    """EP-based sell recommendation."""

    score: PlayerScore
    player: MarketPlayer | None
    expendability: float  # 0-100 (higher = more expendable)
    is_protected: bool
    protection_reason: str | None
    reason: str


@dataclass
class TradePair:
    """Sell->Buy swap recommendation."""

    buy_player: MarketPlayer
    sell_player: MarketPlayer
    buy_score: PlayerScore
    sell_score: PlayerScore
    net_cost: int
    ep_gain: float
    recommended_bid: int | None = None
    metadata: dict | None = None


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
    net_budget_after: int
    is_viable: bool
    ep_impact: float
    reasoning: str
