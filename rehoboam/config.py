"""Configuration management for Rehoboam"""

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    kickbase_email: str = Field(..., description="KICKBASE account email")
    kickbase_password: str = Field(..., description="KICKBASE account password")

    # Trading Configuration
    min_sell_profit_pct: float = Field(
        default=5.0,
        description="Minimum profit percentage to trigger a sell",
    )
    max_loss_pct: float = Field(
        default=-3.0,
        description="Maximum loss percentage before auto-selling",
    )
    min_buy_value_increase_pct: float = Field(
        default=10.0,
        description="Minimum market value increase to consider buying",
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

    # Squad Management Safeguards
    min_squad_size: int = Field(
        default=11,
        description="Minimum squad size to maintain (must be able to field a lineup)",
    )
    never_sell_starters: bool = Field(
        default=True,
        description="Never sell players in your current starting eleven",
    )
    min_points_to_keep: int = Field(
        default=50,
        description="Don't sell high-performing players above this points threshold",
    )

    # Safety Settings
    dry_run: bool = Field(
        default=True,
        description="If True, simulate trades without executing them",
    )


def get_settings() -> Settings:
    """Get application settings"""
    return Settings()
