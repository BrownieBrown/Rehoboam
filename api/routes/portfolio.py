"""Portfolio routes"""

from fastapi import APIRouter, Depends, HTTPException

from api.auth import TokenData, get_current_user
from api.dependencies import get_cached_api, run_sync
from api.models import PortfolioResponse, SquadPlayerResponse
from rehoboam.analyzer import MarketAnalyzer
from rehoboam.config import get_settings
from rehoboam.formation import select_best_eleven
from rehoboam.value_calculator import PlayerValue

router = APIRouter()


@router.get("/squad", response_model=PortfolioResponse)
async def get_squad(current_user: TokenData = Depends(get_current_user)):
    """Get current squad with value tracking"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]
        squad = await run_sync(api.get_squad, league)
        budget_info = await run_sync(api.get_team_info, league)

        settings = get_settings()

        # Create analyzer instance for smart sell recommendations
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=settings.min_buy_value_increase_pct,
            min_sell_profit_pct=settings.min_sell_profit_pct,
            max_loss_pct=settings.max_loss_pct,
            min_value_score_to_buy=settings.min_value_score_to_buy,
        )

        # First pass: Calculate value scores for all players to determine best 11
        player_values = {}
        player_value_scores = {}
        for player in squad:
            try:
                pv = PlayerValue.calculate(player)
                player_values[player.id] = pv.value_score
                player_value_scores[player.id] = pv.value_score
            except Exception:
                player_values[player.id] = player.average_points  # Fallback
                player_value_scores[player.id] = 0.0

        # Determine best 11 for protection
        best_eleven = select_best_eleven(squad, player_values)
        best_eleven_ids = {p.id for p in best_eleven}

        squad_response = []
        total_value = 0
        total_profit_loss = 0

        for player in squad:
            # Use buy_price from player (falls back to market_value if not available)
            purchase_price = player.buy_price if player.buy_price > 0 else player.market_value

            # Calculate profit/loss
            profit_loss = player.market_value - purchase_price
            profit_loss_pct = (profit_loss / purchase_price * 100) if purchase_price > 0 else 0

            total_value += player.market_value
            total_profit_loss += profit_loss

            # Get pre-calculated value score
            value_score = player_value_scores.get(player.id, 0.0)

            # Use smart analyzer for sell recommendations
            # Considers: profit target, stop loss, peak decline, poor performance,
            # difficult schedule, falling trend, and best 11 protection
            is_in_best_eleven = player.id in best_eleven_ids
            analysis = analyzer.analyze_owned_player(
                player=player,
                purchase_price=purchase_price,
                is_in_best_eleven=is_in_best_eleven,
            )
            sell_recommendation = (
                analysis.recommendation if analysis.recommendation == "SELL" else None
            )
            sell_reason = analysis.reason if sell_recommendation else None

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
async def get_balance(current_user: TokenData = Depends(get_current_user)):
    """Get budget and team value"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]
        budget_info = await run_sync(api.get_team_info, league)
        squad = await run_sync(api.get_squad, league)

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
async def get_value_history(current_user: TokenData = Depends(get_current_user)):
    """Get team value history"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        _league = leagues[0]  # noqa: F841 - validates league exists

        # Team value history not available via API - return empty list
        return {"history": []}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get history: {str(e)}") from e
