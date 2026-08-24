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

# League rule for 26/27: at most three players from any one Bundesliga club.
# This is a HARD rule set by the league, not a tuning knob — a squad that
# breaks it is illegal, so it is a module constant rather than a Settings
# field that could be relaxed from .env by accident.
MAX_PLAYERS_PER_CLUB = 3

# Selling instantly to Kickbase returns the FULL market value.
#
# REH-51 asserted 0.95 in its plan and nothing ever checked it. Measured in
# REH-67 across all 151 real flips in `flip_outcomes`, joined to
# `player_mv_history` within a day of the sale: the sell/MV ratio has a hard
# mode of 41 rows at exactly 1.00 and ZERO rows at 0.95 (2 anywhere in
# 0.94-0.96). A 5% haircut would leave a cluster at 0.95; there is none. Ratios
# above 1.00 are sales to other managers at a premium, a different channel.
# `api.sell_player_instant` documents the same: "sell instantly to Kickbase at
# market value".
#
# It lives here, not in `replay/`, so the replay and the live path cannot drift
# apart: REH-67 corrected the replay and left seven live sites on 0.95 for a
# season (REH-79). Understating recovery is NOT harmlessly conservative --
# trade pairs compute `net_cost = bid - sell_recovery`, so it inflates every
# pair's net cost and rejects upgrades the bot can afford, and
# `bidding_strategy.calculate_ep_bid` derives `budget_ceiling` from it.
#
# It does not weaken the kickoff guard. `services/execution.py`'s REH-11 block
# tests `(current_budget - price) < 0` against ACTUAL budget and ignores sell
# recovery entirely, because buy-first-sell-after means the sale has not
# happened when the bid is placed. That protection is independent of this
# number.
INSTANT_SELL_PCT = 1.0


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
    # REH-71. Both default True, which is the behaviour that shipped before
    # these switches existed — they are a control surface, not a decision.
    #
    # REH-71 originally set both False, arguing that flipping is structurally
    # unprofitable because every round trip pays an 11.7% toll. That argument
    # was withdrawn: the 1.117x mean transaction price was measured on
    # manager-to-manager transfers, but the bot only flips is_kickbase_seller()
    # listings (trader.py), where price IS market value and instant sell
    # returns 1.00x (REH-67). A Kickbase-sourced round trip has no structural
    # toll. The league's top two managers finished 1st and 2nd on points AND on
    # transfer profit (+EUR 130.8M and +EUR 164.7M) against our -EUR 55.3M, so
    # our loss is a selection failure, not proof the channel cannot pay.
    #
    # Diagnosing that failure is REH-75; reconstructing what the winners did is
    # REH-72. Turn either switch off from .env if the bleeding needs stopping
    # before those land.
    enable_flip_buys: bool = Field(
        default=True,
        description="Buy players for expected appreciation rather than expected points",
    )
    # Scope note: this switches off profit-taking and loss-cutting only. The
    # dead-weight release inside the same phase (dumping a position-saturated
    # bench player to free a squad slot for a points upgrade) keeps running —
    # it serves points, not profit, and was never part of what REH-71 measured.
    enable_profit_sells: bool = Field(
        default=True,
        description="Take profit / cut losses on squad players against their cost basis",
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
        default=25.0,
        description=(
            "Minimum MARGINAL EP gain (real points) for a market player to count as "
            "an upgrade at all. Re-derived 2026-08-24 against the live market with "
            "the refitted scorer: p50 21.5, p70 23.9, p85 26.1, p95 88.2 over 16 "
            "positive candidates. 25.0 sits just under p85, so roughly the top "
            "fifth of what the market actually offers can qualify. The previous "
            "40.0 was a pre-season estimate taken against a synthetic squad when "
            "no purchasable listings existed; it sat ABOVE p85, so it rejected 14 "
            "of 19 real candidates and left the squad at 11/15 with EUR 62M idle. "
            "Note marginal gain is "
            "relative to YOUR squad: a strong squad finds fewer improvements, so "
            "this gets harder to clear as the season goes well."
        ),
    )
    target_ep_bar: float = Field(
        default=0.0,
        description=(
            "Absolute expected points a market player must reach to count as a "
            "target worth a squad slot, independent of marginal gain against the "
            "current squad. 0.0 disables the bar and preserves pre-2026-08-22 "
            "behaviour. Derive this from the measured marginal-gain and EP "
            "distributions via `rehoboam derive-thresholds` once the market has "
            "repopulated — it reported n=0 on 2026-07-31 and n=9 on 2026-08-22, "
            "neither of which is enough to set it from."
        ),
    )

    uncertain_start_multiplier: float = Field(
        default=0.5,
        description=(
            "How much of a player's start probability survives a Kickbase "
            "'uncertain' flag (status 2). 1.0 ignores the flag, 0.0 treats it as "
            "a hard block. Deliberately a haircut rather than a verdict: status 2 "
            "covers both a player returning to fitness and one falling out of "
            "favour. On 2026-08-22 it covered Fuehrich (market value +15.7% over "
            "30d, returning) and Stark (-36.0%, fading) simultaneously — the code "
            "alone cannot tell them apart. Injuries (status 4/256) are handled "
            "separately and are not affected by this knob."
        ),
    )

    max_status_age_days: float = Field(
        default=60.0,
        description=(
            "Discard a player's last-played availability status when it is older "
            "than this many days, falling back to the availability model's "
            "marginal prior. Must exceed the longest in-season gap (the winter "
            "break, roughly 30 days) and fall below the off-season gap (roughly "
            "90). Without it, the final matchday of the previous season drives "
            "availability for the whole of the next: on 2026-08-22 Pavlovic was "
            "scored P(start)=17% off an unused-sub appearance played 2026-05-16."
        ),
    )

    min_days_to_match_for_starter_swap: int = Field(
        default=3,
        description=(
            "Days before kickoff below which a trade pair may not sell a member "
            "of the best eleven. A pair sells before it bids -- Kickbase counts "
            "open bids toward the 15-player cap, so the sell is what frees the "
            "slot -- which makes the sell certain and the buy only a bid. Losing "
            "that auction leaves the starting eleven short until a later session "
            "refills it, so the swap is only worth risking while sessions remain "
            "before kickoff. Bench sells are unaffected: they cost nothing on the "
            "pitch. An unknown match date counts as no time. See REH-87."
        ),
    )

    # Bid tiers — the marginal-gain bands SmartBidding uses to size an overbid.
    # Fields rather than module constants so they can be re-tuned from `.env`
    # mid-season without shipping a build.
    bid_tier_must_have: float = Field(
        default=62.5,
        description=(
            "Marginal EP gain (real points) at or above which a candidate is a "
            "'must have'. Rounded from p85 = 69.3 measured 2026-07-31. Because "
            "marginal gain is measured against your own squad, this may rarely "
            "fire late in a successful season — the bot going quiet there is the "
            "design working, not a fault. Pre-season estimate."
        ),
    )
    bid_tier_strong_upgrade: float = Field(
        default=37.5,
        description=(
            "Marginal EP gain (real points) at or above which a candidate is a "
            "'strong upgrade'. Rounded from p70 = 52.6 measured 2026-07-31. "
            "Pre-season estimate awaiting live-market validation."
        ),
    )
    bid_full_commit_gain: float = Field(
        default=82.0,
        description=(
            "Marginal EP gain (real points) at which the bot will commit its "
            "maximum share of budget to a single signing. Set to p95 = 82.0 of "
            "the distribution measured 2026-07-31; anchoring here rather than at "
            "the observed maximum (176.2) keeps bid sizing discriminating across "
            "the bulk of real candidates instead of being stretched flat by one "
            "outlier. Pre-season estimate awaiting live-market validation."
        ),
    )
    bid_tier_solid_upgrade: float = Field(
        default=25.0,
        description=(
            "Marginal EP gain (real points) at or above which a candidate is a "
            "'solid upgrade'. Rounded from p50 = 43.1 measured 2026-07-31. "
            "Pre-season estimate awaiting live-market validation."
        ),
    )
    max_overbid_pct: float = Field(
        default=8.0,
        description=(
            "Hard ceiling on how far above market value any bid may go, enforced by "
            "services/safety_gate.check_buy. 8.0 comes from REH-64's measurement: a "
            "3-8% overbid won 50% of auctions and an 8-15% overbid won the same 50%, "
            "so margin beyond ~8% bought almost no win rate while the bot averaged "
            "+12.2% and lost EUR 55.3M across 151 flips. Distinct from SmartBidding's "
            "own internal caps — this is the outer limit nothing may cross."
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

    # Telegram approval gate
    telegram_bot_token: str = Field(
        default="",
        repr=False,
        description="Telegram bot token. Empty disables Telegram entirely.",
    )
    telegram_chat_id: str = Field(
        default="",
        description="Telegram chat to send trade proposals to.",
    )
    telegram_webhook_secret: str = Field(
        default="",
        repr=False,
        description=(
            "Shared secret Telegram echoes in X-Telegram-Bot-Api-Secret-Token. The "
            "approval webhook is a public endpoint that spends money, so a callback "
            "without this header is rejected before anything is read from it."
        ),
    )

    # Daily summary email
    smtp_host: str = Field(default="", description="SMTP host. Empty disables email.")
    smtp_port: int = Field(default=587, description="SMTP port; 587 for STARTTLS.")
    smtp_user: str = Field(default="", repr=False, description="SMTP username.")
    smtp_password: str = Field(default="", repr=False, description="SMTP password or app password.")
    alert_email_to: str = Field(default="", description="Recipient of the daily summary.")


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
