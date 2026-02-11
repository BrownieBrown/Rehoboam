"""Analytics routes"""

from fastapi import APIRouter, Depends, HTTPException

from api.auth import TokenData, get_current_user
from api.dependencies import get_cached_api, run_sync
from api.models import AnalyticsResponse, RecommendationResponse
from rehoboam.analyzer import MarketAnalyzer
from rehoboam.config import POSITION_MINIMUMS, get_settings
from rehoboam.formation import select_best_eleven
from rehoboam.roster_analyzer import RosterAnalyzer
from rehoboam.value_calculator import PlayerValue

router = APIRouter()


@router.get("/recommendations", response_model=AnalyticsResponse)
async def get_recommendations(current_user: TokenData = Depends(get_current_user)):
    """Get buy and sell recommendations"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]
        settings = get_settings()
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=settings.min_buy_value_increase_pct,
            min_sell_profit_pct=settings.min_sell_profit_pct,
            max_loss_pct=settings.max_loss_pct,
            min_value_score_to_buy=settings.min_value_score_to_buy,
        )
        roster_analyzer = RosterAnalyzer()

        # Get current squad
        squad = await run_sync(api.get_squad, league)

        # Calculate value scores for best 11 determination and roster context
        player_values = {}
        for player in squad:
            try:
                pv = PlayerValue.calculate(player)
                player_values[player.id] = pv.value_score
            except Exception:
                player_values[player.id] = player.average_points

        # Analyze roster composition with value scores
        roster_contexts = roster_analyzer.analyze_roster(squad, {}, player_values)

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

        # Determine best 11 for protection
        best_eleven = select_best_eleven(squad, player_values)
        best_eleven_ids = {p.id for p in best_eleven}

        # Generate SELL recommendations using smart analyzer
        sell_recommendations = []
        for player in squad:
            purchase_price = player.buy_price if player.buy_price > 0 else player.market_value
            profit_loss = player.market_value - purchase_price
            profit_loss_pct = (profit_loss / purchase_price * 100) if purchase_price > 0 else 0

            # Use smart analyzer with best 11 protection
            is_in_best_eleven = player.id in best_eleven_ids
            analysis = analyzer.analyze_owned_player(
                player=player,
                purchase_price=purchase_price,
                is_in_best_eleven=is_in_best_eleven,
            )

            if analysis.recommendation == "SELL":
                sell_recommendations.append(
                    RecommendationResponse(
                        player_id=player.id,
                        player_name=f"{player.first_name} {player.last_name}".strip(),
                        position=player.position,
                        team_name=player.team_name,
                        action="SELL",
                        reason=analysis.reason,
                        value_score=analysis.value_score,
                        confidence=analysis.confidence,
                        price=None,
                        market_value=player.market_value,
                        profit_loss_pct=profit_loss_pct,
                    )
                )

        # Generate BUY recommendations from market
        buy_recommendations = []
        market_players = await run_sync(api.get_market, league)

        for player in market_players:
            # Analyze with roster context for position-aware scoring
            roster_context = roster_contexts.get(player.position)
            analysis = analyzer.analyze_market_player(player, roster_context=roster_context)

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
async def get_roster_analysis(current_user: TokenData = Depends(get_current_user)):
    """Get detailed roster composition analysis"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]
        squad = await run_sync(api.get_squad, league)
        roster_analyzer = RosterAnalyzer()

        # Analyze roster
        roster_contexts = roster_analyzer.analyze_roster(squad, {})

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
