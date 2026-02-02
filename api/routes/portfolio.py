"""Portfolio routes"""

import os

from fastapi import APIRouter, HTTPException

from api.dependencies import get_api_for_user, run_sync
from api.models import PortfolioResponse, SquadPlayerResponse
from rehoboam.config import get_settings
from rehoboam.value_calculator import PlayerValue

router = APIRouter()


def get_authenticated_api():
    """Get authenticated API using env credentials"""
    email = os.getenv("KICKBASE_EMAIL")
    password = os.getenv("KICKBASE_PASSWORD")
    if not email or not password:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return get_api_for_user(email, password)


@router.get("/squad", response_model=PortfolioResponse)
async def get_squad():
    """Get current squad with value tracking"""
    try:
        api = await run_sync(get_authenticated_api)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]
        squad = await run_sync(api.get_squad, league.id)
        budget_info = await run_sync(api.get_budget, league.id)

        # Get player stats for purchase prices
        player_stats = {}
        try:
            stats_data = await run_sync(api.get_player_stats, league.id)
            if stats_data:
                player_stats = {str(p.get("id")): p for p in stats_data}
        except Exception:
            pass

        settings = get_settings()

        squad_response = []
        total_value = 0
        total_profit_loss = 0

        for player in squad:
            # Get purchase price from stats
            stats = player_stats.get(player.id, {})
            purchase_price = stats.get("trp", player.market_value)

            # Calculate profit/loss
            profit_loss = player.market_value - purchase_price
            profit_loss_pct = (profit_loss / purchase_price * 100) if purchase_price > 0 else 0

            total_value += player.market_value
            total_profit_loss += profit_loss

            # Calculate value score
            try:
                player_value = PlayerValue.calculate(player)
                value_score = player_value.value_score
            except Exception:
                value_score = 0.0

            # Check for sell recommendation
            sell_recommendation = None
            sell_reason = None
            if profit_loss_pct >= settings.min_sell_profit_pct:
                sell_recommendation = "SELL"
                sell_reason = f"Profit target reached: {profit_loss_pct:.1f}%"
            elif profit_loss_pct <= settings.max_loss_pct:
                sell_recommendation = "SELL"
                sell_reason = f"Stop-loss triggered: {profit_loss_pct:.1f}%"

            squad_response.append(
                SquadPlayerResponse(
                    id=player.id,
                    first_name=player.first_name,
                    last_name=player.last_name,
                    position=player.position,
                    team_name=player.team_name,
                    team_id=player.team_id,
                    market_value=player.market_value,
                    purchase_price=purchase_price,
                    profit_loss=profit_loss,
                    profit_loss_pct=profit_loss_pct,
                    points=player.points,
                    average_points=player.average_points,
                    value_score=value_score,
                    sell_recommendation=sell_recommendation,
                    sell_reason=sell_reason,
                )
            )

        # Sort by value (highest first)
        squad_response.sort(key=lambda x: x.market_value, reverse=True)

        return PortfolioResponse(
            budget=budget_info.get("budget", 0),
            team_value=total_value,
            total_profit_loss=total_profit_loss,
            squad_size=len(squad),
            squad=squad_response,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get squad: {str(e)}") from e


@router.get("/balance")
async def get_balance():
    """Get budget and team value"""
    try:
        api = await run_sync(get_authenticated_api)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]
        budget_info = await run_sync(api.get_budget, league.id)
        squad = await run_sync(api.get_squad, league.id)

        team_value = sum(p.market_value for p in squad)

        return {
            "budget": budget_info.get("budget", 0),
            "team_value": team_value,
            "total_assets": budget_info.get("budget", 0) + team_value,
            "squad_size": len(squad),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get balance: {str(e)}") from e


@router.get("/history")
async def get_value_history():
    """Get team value history"""
    try:
        api = await run_sync(get_authenticated_api)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]

        # Try to get historical data
        history = []
        try:
            history_data = await run_sync(api.get_team_value_history, league.id)
            if history_data:
                history = [
                    {"date": item.get("date"), "value": item.get("value")} for item in history_data
                ]
        except Exception:
            pass

        return {"history": history}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get history: {str(e)}") from e
