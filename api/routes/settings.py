"""Settings routes"""

from fastapi import APIRouter

from api.models import SettingsResponse, SettingsUpdate
from rehoboam.config import get_settings

router = APIRouter()

# In-memory settings override (in production, would be stored in DB)
_settings_override: dict = {}


@router.get("", response_model=SettingsResponse)
async def get_user_settings():
    """Get current settings"""
    settings = get_settings()

    return SettingsResponse(
        min_sell_profit_pct=_settings_override.get(
            "min_sell_profit_pct", settings.min_sell_profit_pct
        ),
        max_loss_pct=_settings_override.get("max_loss_pct", settings.max_loss_pct),
        min_buy_value_increase_pct=_settings_override.get(
            "min_buy_value_increase_pct", settings.min_buy_value_increase_pct
        ),
        min_value_score_to_buy=_settings_override.get(
            "min_value_score_to_buy", settings.min_value_score_to_buy
        ),
        max_player_cost=_settings_override.get("max_player_cost", settings.max_player_cost),
        reserve_budget=_settings_override.get("reserve_budget", settings.reserve_budget),
        dry_run=_settings_override.get("dry_run", settings.dry_run),
    )


@router.put("", response_model=SettingsResponse)
async def update_settings(update: SettingsUpdate):
    """Update settings (in-memory only for now)"""
    global _settings_override

    if update.min_sell_profit_pct is not None:
        _settings_override["min_sell_profit_pct"] = update.min_sell_profit_pct

    if update.max_loss_pct is not None:
        _settings_override["max_loss_pct"] = update.max_loss_pct

    if update.min_value_score_to_buy is not None:
        _settings_override["min_value_score_to_buy"] = max(50.0, update.min_value_score_to_buy)

    if update.max_player_cost is not None:
        _settings_override["max_player_cost"] = update.max_player_cost

    if update.reserve_budget is not None:
        _settings_override["reserve_budget"] = update.reserve_budget

    if update.dry_run is not None:
        _settings_override["dry_run"] = update.dry_run

    return await get_user_settings()


@router.post("/reset")
async def reset_settings():
    """Reset settings to defaults"""
    global _settings_override
    _settings_override = {}
    return {"message": "Settings reset to defaults"}
