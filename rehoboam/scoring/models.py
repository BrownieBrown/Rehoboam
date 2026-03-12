"""Data models for the EP-first scoring pipeline."""

from dataclasses import dataclass, field

from ..kickbase_client import MarketPlayer
from ..matchup_analyzer import DoubleGameweekInfo, TeamStrength


@dataclass
class DataQuality:
    """Data quality assessment for a player's score."""

    grade: str
    games_played: int
    consistency: float
    has_fixture_data: bool
    has_lineup_data: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class PlayerScore:
    """Unified expected points score for a player (0-180 scale)."""

    player_id: str
    expected_points: float
    data_quality: DataQuality
    base_points: float
    consistency_bonus: float
    lineup_bonus: float
    fixture_bonus: float
    form_bonus: float
    minutes_bonus: float
    dgw_multiplier: float
    is_dgw: bool
    next_opponent: str | None
    notes: list[str] = field(default_factory=list)
    current_price: int = 0
    market_value: int = 0
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
    roster_bonus: float
    reason: str

    @property
    def effective_ep(self) -> float:
        return self.score.expected_points + self.roster_bonus


@dataclass
class SellRecommendation:
    """A recommended player to sell."""

    player: MarketPlayer
    score: PlayerScore
    is_protected: bool
    protection_reason: str | None
    budget_recovery: int


@dataclass
class TradePair:
    """A sell->buy swap recommendation."""

    buy_player: MarketPlayer
    sell_player: MarketPlayer
    buy_score: PlayerScore
    sell_score: PlayerScore

    @property
    def net_cost(self) -> int:
        return self.buy_score.current_price - self.sell_score.market_value

    @property
    def ep_gain(self) -> float:
        return self.buy_score.expected_points - self.sell_score.expected_points
