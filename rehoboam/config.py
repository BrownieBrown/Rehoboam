"""Configuration management for Rehoboam"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Minimum required players per position for a valid starting 11
# Beyond these minimums, any position can be used to fill remaining spots
POSITION_MINIMUMS = {
    "Goalkeeper": 1,
    "Defender": 3,
    "Midfielder": 2,
    "Forward": 1,
}

# Minimum value score improvement to consider a market player a significant upgrade
# over an existing squad player
MIN_UPGRADE_THRESHOLD = 10.0

# Maximum lineup probability (1-5) to consider buying a player.
# 1 = starter, 2 = rotation, 3 = bench; 4+ = unlikely to play
MAX_LINEUP_PROB_FOR_BUY = 3


# Find .env file - look in current directory first, then in home directory
def find_env_file() -> Path:
    """Find the .env file in current directory or user's home directory"""
    # Check current directory
    current_dir_env = Path.cwd() / ".env"
    if current_dir_env.exists():
        return current_dir_env

    # Check home directory for .rehoboam.env
    home_env = Path.home() / ".rehoboam.env"
    if home_env.exists():
        return home_env

    # Default to current directory (will use environment variables only if not found)
    return current_dir_env


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    model_config = SettingsConfigDict(
        env_file=find_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # KICKBASE Credentials
    kickbase_email: str = Field(..., repr=False, description="KICKBASE account email")
    kickbase_password: str = Field(..., repr=False, description="KICKBASE account password")

    # Trading Configuration
    min_sell_profit_pct: float = Field(
        default=15.0,
        description="Minimum profit percentage to trigger a sell (raised to keep winners longer)",
    )
    max_loss_pct: float = Field(
        default=-15.0,
        description="Maximum loss percentage before triggering sell (relaxed — daily fluctuations of 3-10% are normal)",
    )
    min_buy_value_increase_pct: float = Field(
        default=10.0,
        description="Minimum market value increase to consider buying",
    )
    min_value_score_to_buy: float = Field(
        default=55.0,
        description="Minimum value score (0-100) to consider buying a player (raised for quality-first strategy)",
    )

    # Budget Management
    max_player_cost: int = Field(
        default=5_000_000,
        description="Maximum amount to spend on a single player",
    )
    reserve_budget: int = Field(
        default=1_000_000,
        description="Always keep this much in reserve",
    )
    max_debt_pct_of_team_value: float = Field(
        default=60.0,
        description="Maximum debt allowed as % of team value (can go negative up to this)",
    )

    # Squad Management Safeguards
    min_squad_size: int = Field(
        default=13,
        description=(
            "NOT YET ENFORCED — sell floor only, and nothing currently reads "
            "this to stop a sell (SquadOptimizer assigns it and never checks "
            "it; wiring an actual sell-side guard is week-4 work). Set to 11 "
            "(a full lineup) + 2 injury cover, which also leaves exactly 2 "
            "open bid slots under Kickbase's 15-player cap. Does NOT drive "
            "emergency-buy triggering: comparing raw headcount to a floor above "
            "11 flags perfectly fieldable 11-12 player squads as emergencies "
            "(fired on 93% of last season's sessions once tried). The emergency "
            "trigger is a separate, position-aware fieldability check — see "
            "formation.can_fill_starting_eleven — decoupled from this value."
        ),
    )
    min_upgrade_value_score_diff: float = Field(
        default=15.0,
        description="Minimum value score difference to consider a replacement an upgrade",
    )

    # EP-First Settings
    #
    # These are on the v2 scale: REAL Kickbase matchday points, not the old
    # 0-100 index. Every value below is a PRE-SEASON ESTIMATE awaiting
    # live-market validation. `derive-thresholds` measured n=0 on 2026-07-31
    # because no purchasable listings exist before the season opens
    # (2026-08-28), so the numbers come instead from a controller measurement
    # over the full 473-player universe scored against a synthetic mid-table
    # 15-man squad: marginal-gain p50 43.1, p70 52.6, p85 69.3, with 309 of
    # 473 candidates showing a positive gain.
    #
    # Re-run `rehoboam derive-thresholds` once the market repopulates and tune
    # these in `.env` — they are Settings fields precisely so the first real
    # evidence, which arrives mid-season, does not require a new build.
    min_expected_points_to_buy: float = Field(
        default=35.0,
        description=(
            "Minimum ABSOLUTE expected points (real points) to consider buying a "
            "player. Set from the v2 EP median of 34.8 measured 2026-07-31, "
            "preserving the original intent of 'do not buy a player who is not at "
            "least mid-table'. Pre-season estimate awaiting live-market validation."
        ),
    )
    min_ep_upgrade_threshold: float = Field(
        default=40.0,
        description=(
            "Minimum MARGINAL EP gain (real points) for a market player to count as "
            "an upgrade at all. Rounded from the p50 of 43.1 measured 2026-07-31 "
            "over the 473-player universe against a mid-table squad. Pre-season "
            "estimate awaiting live-market validation. Note marginal gain is "
            "relative to YOUR squad: a strong squad finds fewer improvements, so "
            "this gets harder to clear as the season goes well."
        ),
    )

    # Bid tiers — the marginal-gain bands SmartBidding uses to size an overbid.
    # Fields rather than module constants so they can be re-tuned from `.env`
    # mid-season without shipping a build.
    bid_tier_must_have: float = Field(
        default=70.0,
        description=(
            "Marginal EP gain (real points) at or above which a candidate is a "
            "'must have'. Rounded from p85 = 69.3 measured 2026-07-31. Because "
            "marginal gain is measured against your own squad, this may rarely "
            "fire late in a successful season — the bot going quiet there is the "
            "design working, not a fault. Pre-season estimate."
        ),
    )
    bid_tier_strong_upgrade: float = Field(
        default=53.0,
        description=(
            "Marginal EP gain (real points) at or above which a candidate is a "
            "'strong upgrade'. Rounded from p70 = 52.6 measured 2026-07-31. "
            "Pre-season estimate awaiting live-market validation."
        ),
    )
    bid_tier_solid_upgrade: float = Field(
        default=43.0,
        description=(
            "Marginal EP gain (real points) at or above which a candidate is a "
            "'solid upgrade'. Rounded from p50 = 43.1 measured 2026-07-31. "
            "Pre-season estimate awaiting live-market validation."
        ),
    )

    # Auto Trading Limits
    auto_max_trades_normal: int = Field(
        default=10,
        description="Max trades per auto session in normal mode",
    )
    auto_max_trades_aggressive: int = Field(
        default=15,
        description="Max trades per auto session in aggressive mode",
    )

    # Wash-trade + churn guards
    wash_trade_block_hours: float = Field(
        default=168.0,  # 7 days
        description="Refuse to re-bid on a player we sold within this window",
    )
    min_hold_hours_before_sell: float = Field(
        default=48.0,
        description="Refuse to instant-sell a player held less than this (overridden when budget is critically negative)",
    )

    # Safety Settings
    dry_run: bool = Field(
        default=True,
        description="If True, simulate trades without executing them",
    )


def get_settings() -> Settings:
    """Get application settings"""
    return Settings()


class AzureBlobSettings(BaseSettings):
    """Azure Blob Storage settings — used by both the Azure Function and the
    `rehoboam fetch-azure-state` / `push-azure-state` CLI commands.

    Kept separate from `Settings` so debugging commands don't require
    KICKBASE credentials to be present in `.env`.
    """

    model_config = SettingsConfigDict(
        env_file=find_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    azure_storage_connection_string: str | None = Field(
        default=None,
        description="Azure Blob Storage connection string (set in .env or app settings)",
    )
    blob_container: str = Field(
        default="rehoboam-data",
        description="Container holding the persisted SQLite databases",
    )
