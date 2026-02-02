"""Market routes"""

import os
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from api.dependencies import get_api_for_user, run_sync
from api.models import MarketPlayerResponse, PlayerDetailResponse, TrendDataPoint
from rehoboam.analyzer import MarketAnalyzer
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


@router.get("/players", response_model=list[MarketPlayerResponse])
async def get_market_players(
    position: str | None = Query(None, description="Filter by position"),
    min_score: float = Query(0, description="Minimum value score"),
    sort_by: str = Query("value_score", description="Sort field"),
    limit: int = Query(50, description="Max results"),
):
    """Get market players with analysis"""
    try:
        api = await run_sync(get_authenticated_api)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]
        market_players = await run_sync(api.get_market, league.id)
        settings = get_settings()
        analyzer = MarketAnalyzer(settings)

        results = []
        for player in market_players:
            # Calculate value
            try:
                player_value = PlayerValue.calculate(player)
                value_score = player_value.value_score
            except Exception:
                value_score = 0.0

            # Get trend data
            trend_direction = None
            trend_pct = None
            try:
                trend = await run_sync(api.get_player_trend, player.id)
                if trend and "direction" in trend:
                    trend_direction = trend.get("direction")
                    trend_pct = trend.get("change_pct", 0)
            except Exception:
                pass

            # Analyze
            analysis = analyzer.analyze_market_player(player)

            # Apply filters
            if position and player.position != position:
                continue
            if value_score < min_score:
                continue

            results.append(
                MarketPlayerResponse(
                    id=player.id,
                    first_name=player.first_name,
                    last_name=player.last_name,
                    position=player.position,
                    team_name=player.team_name,
                    team_id=player.team_id,
                    market_value=player.market_value,
                    price=player.price,
                    expiry=player.expiry if hasattr(player, "expiry") else None,
                    seller=player.seller if hasattr(player, "seller") else None,
                    points=player.points,
                    average_points=player.average_points,
                    value_score=analysis.value_score,
                    recommendation=analysis.recommendation,
                    confidence=analysis.confidence,
                    trend_direction=trend_direction,
                    trend_pct=trend_pct,
                    factors={f.name: f.score for f in analysis.factors},
                    roster_impact=None,
                )
            )

        # Sort
        if sort_by == "value_score":
            results.sort(key=lambda x: x.value_score, reverse=True)
        elif sort_by == "price":
            results.sort(key=lambda x: x.price)
        elif sort_by == "market_value":
            results.sort(key=lambda x: x.market_value, reverse=True)

        return results[:limit]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get market: {str(e)}") from e


@router.get("/players/{player_id}", response_model=PlayerDetailResponse)
async def get_player_detail(player_id: str):
    """Get detailed player analysis"""
    try:
        api = await run_sync(get_authenticated_api)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        _league = leagues[0]  # noqa: F841 - validates league exists

        # Get player info
        player_info = await run_sync(api.get_player_info, player_id)
        if not player_info:
            raise HTTPException(status_code=404, detail="Player not found")

        # Get trend history
        trend_history = []
        try:
            history = await run_sync(api.get_player_market_value_history, player_id)
            if history:
                for item in history[-30:]:  # Last 30 days
                    trend_history.append(
                        TrendDataPoint(
                            date=item.get("date", ""),
                            value=item.get("value", 0),
                        )
                    )
        except Exception:
            pass

        # Calculate value
        try:
            player_value = PlayerValue.calculate(player_info)
            value_score = player_value.value_score
            games_played = player_value.games_played
        except Exception:
            value_score = 0.0
            games_played = None

        # Analyze
        settings = get_settings()
        analyzer = MarketAnalyzer(settings)
        analysis = analyzer.analyze_market_player(player_info)

        # Get trend
        trend_direction = None
        trend_pct = None
        try:
            trend = await run_sync(api.get_player_trend, player_id)
            if trend:
                trend_direction = trend.get("direction")
                trend_pct = trend.get("change_pct", 0)
        except Exception:
            pass

        return PlayerDetailResponse(
            id=player_info.id,
            first_name=player_info.first_name,
            last_name=player_info.last_name,
            position=player_info.position,
            team_name=player_info.team_name,
            team_id=player_info.team_id,
            market_value=player_info.market_value,
            points=player_info.points,
            average_points=player_info.average_points,
            games_played=games_played,
            value_score=value_score,
            recommendation=analysis.recommendation,
            confidence=analysis.confidence,
            factors={f.name: f.score for f in analysis.factors},
            factor_details=[
                {"name": f.name, "score": f.score, "reason": f.reason} for f in analysis.factors
            ],
            trend_direction=trend_direction,
            trend_pct=trend_pct,
            trend_history=trend_history,
            roster_impact=None,
            replaces_player=None,
            value_score_gain=None,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get player detail: {str(e)}") from e


@router.get("/trends")
async def get_market_trends():
    """Get market trend summary"""
    try:
        api = await run_sync(get_authenticated_api)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]
        market_players = await run_sync(api.get_market, league.id)

        rising = 0
        falling = 0
        stable = 0
        total_value = 0

        for player in market_players:
            total_value += player.market_value
            try:
                trend = await run_sync(api.get_player_trend, player.id)
                if trend:
                    direction = trend.get("direction", "stable")
                    if direction == "rising":
                        rising += 1
                    elif direction == "falling":
                        falling += 1
                    else:
                        stable += 1
            except Exception:
                stable += 1

        return {
            "total_players": len(market_players),
            "total_value": total_value,
            "rising": rising,
            "falling": falling,
            "stable": stable,
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get trends: {str(e)}") from e
