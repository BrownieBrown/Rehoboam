"""Analytics routes"""

import os

from fastapi import APIRouter, HTTPException

from api.dependencies import get_api_for_user, run_sync
from api.models import AnalyticsResponse, RecommendationResponse
from rehoboam.analyzer import MarketAnalyzer
from rehoboam.config import POSITION_MINIMUMS, get_settings
from rehoboam.roster_analyzer import RosterAnalyzer
from rehoboam.value_calculator import PlayerValue

router = APIRouter()


def get_authenticated_api():
    """Get authenticated API using env credentials"""
    email = os.getenv("KICKBASE_EMAIL")
    password = os.getenv("KICKBASE_PASSWORD")
    if not email or not password:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return get_api_for_user(email, password)


@router.get("/recommendations", response_model=AnalyticsResponse)
async def get_recommendations():
    """Get buy and sell recommendations"""
    try:
        api = await run_sync(get_authenticated_api)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]
        settings = get_settings()
        analyzer = MarketAnalyzer(settings)
        roster_analyzer = RosterAnalyzer()

        # Get current squad
        squad = await run_sync(api.get_squad, league.id)

        # Get player stats for purchase prices
        player_stats = {}
        try:
            stats_data = await run_sync(api.get_player_stats, league.id)
            if stats_data:
                player_stats = {str(p.get("id")): p for p in stats_data}
        except Exception:
            pass

        # Analyze roster composition
        roster_contexts = roster_analyzer.analyze_roster(squad, player_stats)

        # Count positions
        position_counts = {}
        for player in squad:
            pos = player.position
            position_counts[pos] = position_counts.get(pos, 0) + 1

        # Find roster gaps
        roster_gaps = []
        for position, minimum in POSITION_MINIMUMS.items():
            current = position_counts.get(position, 0)
            if current < minimum:
                roster_gaps.append(f"{position}: {current}/{minimum}")

        # Generate SELL recommendations from squad
        sell_recommendations = []
        for player in squad:
            stats = player_stats.get(player.id, {})
            purchase_price = stats.get("trp", player.market_value)
            profit_loss = player.market_value - purchase_price
            profit_loss_pct = (profit_loss / purchase_price * 100) if purchase_price > 0 else 0

            # Calculate value score
            try:
                player_value = PlayerValue.calculate(player)
                value_score = player_value.value_score
            except Exception:
                value_score = 0.0

            # Check sell conditions
            if profit_loss_pct >= settings.min_sell_profit_pct:
                sell_recommendations.append(
                    RecommendationResponse(
                        player_id=player.id,
                        player_name=f"{player.first_name} {player.last_name}".strip(),
                        position=player.position,
                        team_name=player.team_name,
                        action="SELL",
                        reason=f"Take profit: {profit_loss_pct:.1f}% gain",
                        value_score=value_score,
                        confidence=0.8,
                        price=None,
                        market_value=player.market_value,
                        profit_loss_pct=profit_loss_pct,
                    )
                )
            elif profit_loss_pct <= settings.max_loss_pct:
                sell_recommendations.append(
                    RecommendationResponse(
                        player_id=player.id,
                        player_name=f"{player.first_name} {player.last_name}".strip(),
                        position=player.position,
                        team_name=player.team_name,
                        action="SELL",
                        reason=f"Stop-loss: {profit_loss_pct:.1f}% loss",
                        value_score=value_score,
                        confidence=0.9,
                        price=None,
                        market_value=player.market_value,
                        profit_loss_pct=profit_loss_pct,
                    )
                )

        # Generate BUY recommendations from market
        buy_recommendations = []
        market_players = await run_sync(api.get_market, league.id)

        for player in market_players:
            # Analyze
            analysis = analyzer.analyze_market_player(player)

            if (
                analysis.recommendation == "BUY"
                and analysis.value_score >= settings.min_value_score_to_buy
            ):
                # Get roster impact
                roster_context = roster_contexts.get(player.position)
                roster_impact = roster_analyzer.get_roster_impact(
                    player, analysis.value_score, roster_context
                )

                buy_recommendations.append(
                    RecommendationResponse(
                        player_id=player.id,
                        player_name=f"{player.first_name} {player.last_name}".strip(),
                        position=player.position,
                        team_name=player.team_name,
                        action="BUY",
                        reason=f"{roster_impact.reason}" if roster_impact else "Good value",
                        value_score=analysis.value_score,
                        confidence=analysis.confidence,
                        price=player.price,
                        market_value=player.market_value,
                        profit_loss_pct=None,
                    )
                )

        # Sort recommendations
        buy_recommendations.sort(key=lambda x: x.value_score, reverse=True)
        sell_recommendations.sort(key=lambda x: abs(x.profit_loss_pct or 0), reverse=True)

        return AnalyticsResponse(
            buy_recommendations=buy_recommendations[:10],  # Top 10
            sell_recommendations=sell_recommendations,
            roster_gaps=roster_gaps,
            position_counts=position_counts,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get recommendations: {str(e)}"
        ) from e


@router.get("/roster-impact")
async def get_roster_analysis():
    """Get detailed roster composition analysis"""
    try:
        api = await run_sync(get_authenticated_api)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]
        squad = await run_sync(api.get_squad, league.id)
        roster_analyzer = RosterAnalyzer()

        # Get player stats
        player_stats = {}
        try:
            stats_data = await run_sync(api.get_player_stats, league.id)
            if stats_data:
                player_stats = {str(p.get("id")): p for p in stats_data}
        except Exception:
            pass

        # Analyze roster
        roster_contexts = roster_analyzer.analyze_roster(squad, player_stats)

        result = {}
        for position, context in roster_contexts.items():
            result[position] = {
                "current_count": context.current_count,
                "minimum_count": context.minimum_count,
                "is_below_minimum": context.is_below_minimum,
                "weakest_player": (
                    context.weakest_player["name"] if context.weakest_player else None
                ),
                "weakest_score": (
                    context.weakest_player["value_score"] if context.weakest_player else None
                ),
                "players": [
                    {
                        "name": p["name"],
                        "value_score": p["value_score"],
                        "market_value": p["current_value"],
                    }
                    for p in context.existing_players
                ],
            }

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to analyze roster: {str(e)}") from e
