"""Pydantic models for API responses"""

from datetime import datetime

from pydantic import BaseModel


class Token(BaseModel):
    """JWT token response"""

    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class UserInfo(BaseModel):
    """Authenticated user info"""

    email: str
    league_id: str
    league_name: str
    team_name: str
    budget: int
    team_value: int


class PlayerBase(BaseModel):
    """Base player info"""

    id: str
    first_name: str
    last_name: str
    position: str
    team_name: str
    team_id: str
    market_value: int
    points: int
    average_points: float


class MarketPlayerResponse(BaseModel):
    """Market player with analysis"""

    id: str
    first_name: str
    last_name: str
    position: str
    team_name: str
    team_id: str
    market_value: int
    price: int
    expiry: datetime | None
    seller: str | None
    points: int
    average_points: float
    # Analysis
    value_score: float
    recommendation: str
    confidence: float
    trend_direction: str | None
    trend_pct: float | None
    factors: dict[str, float]
    roster_impact: str | None


class SquadPlayerResponse(BaseModel):
    """Squad player with value tracking"""

    id: str
    first_name: str
    last_name: str
    position: str
    team_name: str
    team_id: str
    market_value: int
    purchase_price: int
    profit_loss: int
    profit_loss_pct: float
    points: int
    average_points: float
    value_score: float
    sell_recommendation: str | None
    sell_reason: str | None


class PortfolioResponse(BaseModel):
    """Portfolio overview"""

    budget: int
    team_value: int
    total_profit_loss: int
    squad_size: int
    squad: list[SquadPlayerResponse]


class RecommendationResponse(BaseModel):
    """Buy/sell recommendation"""

    player_id: str
    player_name: str
    position: str
    team_name: str
    action: str  # BUY, SELL, HOLD
    reason: str
    value_score: float
    confidence: float
    price: int | None
    market_value: int
    profit_loss_pct: float | None


class AnalyticsResponse(BaseModel):
    """Analytics overview"""

    buy_recommendations: list[RecommendationResponse]
    sell_recommendations: list[RecommendationResponse]
    roster_gaps: list[str]
    position_counts: dict[str, int]


class BidRequest(BaseModel):
    """Request to place a bid"""

    player_id: str
    amount: int
    live: bool = False


class BidResponse(BaseModel):
    """Bid result"""

    success: bool
    player_id: str
    player_name: str
    amount: int
    message: str
    dry_run: bool


class SellRequest(BaseModel):
    """Request to list player for sale"""

    player_id: str
    price: int
    live: bool = False


class SellResponse(BaseModel):
    """Sell listing result"""

    success: bool
    player_id: str
    player_name: str
    price: int
    message: str
    dry_run: bool


class SettingsResponse(BaseModel):
    """User settings"""

    min_sell_profit_pct: float
    max_loss_pct: float
    min_buy_value_increase_pct: float
    min_value_score_to_buy: float
    max_player_cost: int
    reserve_budget: int
    dry_run: bool


class SettingsUpdate(BaseModel):
    """Settings update request"""

    min_sell_profit_pct: float | None = None
    max_loss_pct: float | None = None
    min_value_score_to_buy: float | None = None
    max_player_cost: int | None = None
    reserve_budget: int | None = None
    dry_run: bool | None = None


class TrendDataPoint(BaseModel):
    """Single point in trend data"""

    date: str
    value: int


class PlayerDetailResponse(BaseModel):
    """Detailed player analysis"""

    id: str
    first_name: str
    last_name: str
    position: str
    team_name: str
    team_id: str
    market_value: int
    points: int
    average_points: float
    games_played: int | None
    # Analysis
    value_score: float
    recommendation: str
    confidence: float
    factors: dict[str, float]
    factor_details: list[dict]
    # Trends
    trend_direction: str | None
    trend_pct: float | None
    trend_history: list[TrendDataPoint]
    # Roster context
    roster_impact: str | None
    replaces_player: str | None
    value_score_gain: float | None
