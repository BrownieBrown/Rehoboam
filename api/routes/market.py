"""Market routes"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import TokenData, get_current_user
from api.dependencies import get_cached_api, run_sync
from api.models import (
    MarketPlayerResponse,
    MatchupResponse,
    PlayerDetailResponse,
    PlayerFullResponse,
    PredictionResponse,
    RiskMetricsResponse,
    ScheduleResponse,
    TrendDataPoint,
)
from rehoboam.analyzer import MarketAnalyzer
from rehoboam.config import get_settings
from rehoboam.enhanced_analyzer import EnhancedAnalyzer
from rehoboam.matchup_analyzer import MatchupAnalyzer
from rehoboam.risk_analyzer import RiskAnalyzer
from rehoboam.roster_analyzer import RosterAnalyzer
from rehoboam.value_calculator import PlayerValue

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/players", response_model=list[MarketPlayerResponse])
async def get_market_players(
    current_user: TokenData = Depends(get_current_user),
    position: str | None = Query(None, description="Filter by position"),
    min_score: float = Query(0, description="Minimum value score"),
    sort_by: str = Query("value_score", description="Sort field"),
    limit: int = Query(50, description="Max results"),
):
    """Get market players with analysis"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]
        market_players = await run_sync(api.get_market, league)
        settings = get_settings()
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=settings.min_buy_value_increase_pct,
            min_sell_profit_pct=settings.min_sell_profit_pct,
            max_loss_pct=settings.max_loss_pct,
            min_value_score_to_buy=settings.min_value_score_to_buy,
        )
        roster_analyzer = RosterAnalyzer()

        # Get squad for roster context
        squad = await run_sync(api.get_squad, league)

        # Calculate squad value scores for roster context
        squad_values = {}
        for p in squad:
            try:
                pv = PlayerValue.calculate(p)
                squad_values[p.id] = pv.value_score
            except Exception:
                squad_values[p.id] = p.average_points

        # Get roster contexts for each position
        roster_contexts = roster_analyzer.analyze_roster(squad, {}, squad_values)

        results = []
        for player in market_players:
            # Calculate value
            try:
                player_value = PlayerValue.calculate(player)
                value_score = player_value.value_score
            except Exception:
                value_score = 0.0

            # Trend data not available via API
            trend_direction = None
            trend_pct = None

            # Analyze with roster context for position-aware scoring
            roster_context = roster_contexts.get(player.position)
            analysis = analyzer.analyze_market_player(player, roster_context=roster_context)

            # Get roster impact for display
            roster_impact = roster_analyzer.get_roster_impact(
                player, analysis.value_score, roster_context
            )
            roster_impact_str = roster_impact.reason if roster_impact else None

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
                    roster_impact=roster_impact_str,
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
async def get_player_detail(
    player_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Get detailed player analysis"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]

        # Get player info
        player_info = await run_sync(api.get_player_info, league, player_id)
        if not player_info:
            raise HTTPException(status_code=404, detail="Player not found")

        # Get trend history
        trend_history = []
        try:
            history = await run_sync(api.get_player_market_value_history, league, player_id)
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
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=settings.min_buy_value_increase_pct,
            min_sell_profit_pct=settings.min_sell_profit_pct,
            max_loss_pct=settings.max_loss_pct,
            min_value_score_to_buy=settings.min_value_score_to_buy,
        )
        analysis = analyzer.analyze_market_player(player_info)

        # Trend data not available via API
        trend_direction = None
        trend_pct = None

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
                {"name": f.name, "score": f.score, "reason": f.description}
                for f in analysis.factors
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


@router.get("/players/{player_id}/full", response_model=PlayerFullResponse)
async def get_player_full_detail(
    player_id: str,
    current_user: TokenData = Depends(get_current_user),
    current_price: int | None = Query(None, description="Current market price if on market"),
):
    """Get comprehensive player detail with predictions, risk metrics, and schedule"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]

        # 1. Get player info
        player_info = await run_sync(api.get_player_info, league, player_id)
        if not player_info:
            raise HTTPException(status_code=404, detail="Player not found")

        # 2. Get market value history (30+ days) using competition endpoint
        trend_history = []
        price_history = []
        try:
            # Use competition-based endpoint which returns complete historical data
            raw_history = await run_sync(
                api.client.get_player_market_value_history_v2, player_id, 92  # 3 months
            )
            items = raw_history.get("it", []) if raw_history else []
            if items:
                for item in items[-30:]:  # Last 30 days
                    # dt is days since epoch, mv is market value
                    trend_history.append(
                        TrendDataPoint(
                            date=str(item.get("dt", "")),
                            value=item.get("mv", 0),
                        )
                    )
                    price_history.append(item.get("mv", 0))
        except Exception as e:
            logger.warning(f"Failed to get price history: {e}")

        # 3. Check if on market / in squad
        is_on_market = False
        is_in_squad = False
        market_price = current_price

        try:
            market_players = await run_sync(api.get_market, league)
            for mp in market_players:
                if mp.id == player_id:
                    is_on_market = True
                    market_price = mp.price
                    break
        except Exception:
            pass

        squad = []
        try:
            squad = await run_sync(api.get_squad, league)
            for sp in squad:
                if sp.id == player_id:
                    is_in_squad = True
                    break
        except Exception:
            pass

        # 4. Calculate value score and basic analysis
        try:
            player_value = PlayerValue.calculate(player_info)
            games_played = player_value.games_played
            performance_volatility = player_value.consistency_score or 0.5
        except Exception as e:
            logger.warning(f"PlayerValue.calculate failed: {e}")
            games_played = None
            performance_volatility = 0.5

        # Run analyzer WITHOUT roster context for pure fundamental analysis
        # This gives the "true" recommendation based on player value alone
        settings = get_settings()
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=settings.min_buy_value_increase_pct,
            min_sell_profit_pct=settings.min_sell_profit_pct,
            max_loss_pct=settings.max_loss_pct,
            min_value_score_to_buy=settings.min_value_score_to_buy,
        )
        try:
            analysis = analyzer.analyze_market_player(player_info)
            analysis_value_score = analysis.value_score
            analysis_recommendation = analysis.recommendation
            analysis_confidence = analysis.confidence
            analysis_factors = {f.name: f.score for f in analysis.factors}
            analysis_factor_details = [
                {"name": f.name, "score": f.score, "reason": f.description}
                for f in analysis.factors
            ]
        except Exception as e:
            logger.warning(f"MarketAnalyzer.analyze_market_player failed: {e}")
            analysis_value_score = 0.0
            analysis_recommendation = "HOLD"
            analysis_confidence = 0.0
            analysis_factors = {}
            analysis_factor_details = []

        # Also calculate roster-adjusted values for comparison with Market/Dashboard
        roster_recommendation = None
        roster_value_score = None
        roster_impact_str = None
        if squad:
            try:
                roster_analyzer = RosterAnalyzer()
                squad_values = {}
                for p in squad:
                    try:
                        pv = PlayerValue.calculate(p)
                        squad_values[p.id] = pv.value_score
                    except Exception:
                        squad_values[p.id] = p.average_points

                roster_contexts = roster_analyzer.analyze_roster(squad, {}, squad_values)
                roster_context = roster_contexts.get(player_info.position)

                if roster_context:
                    # Analyze with roster context
                    roster_analysis = analyzer.analyze_market_player(
                        player_info, roster_context=roster_context
                    )
                    roster_recommendation = roster_analysis.recommendation
                    roster_value_score = roster_analysis.value_score

                    # Get roster impact description
                    roster_impact = roster_analyzer.get_roster_impact(
                        player_info, analysis_value_score, roster_context
                    )
                    roster_impact_str = roster_impact.reason if roster_impact else None
            except Exception as e:
                logger.warning(f"Roster-adjusted analysis failed: {e}")

        # Trend data
        trend_direction = None
        trend_pct = None
        if len(price_history) >= 2:
            recent = price_history[-1] if price_history else 0
            older = price_history[0] if price_history else 0
            if older > 0:
                trend_pct = ((recent - older) / older) * 100
                if trend_pct > 5:
                    trend_direction = "rising"
                elif trend_pct < -5:
                    trend_direction = "falling"
                else:
                    trend_direction = "stable"

        # 5. Run EnhancedAnalyzer for predictions
        predictions_response = None
        try:
            enhanced_analyzer = EnhancedAnalyzer()
            # Use trend data if we have ANY price history (not just 7+)
            # This enables predictions even with limited data
            trend_data = {
                "has_data": len(price_history) >= 2,  # Lowered from 7 to 2
                "trend_pct": trend_pct or 0,
                "long_term_pct": trend_pct or 0,
                "peak_value": max(price_history) if price_history else player_info.market_value,
            }
            prediction = enhanced_analyzer.predict_player_value(
                player_info,
                trend_data=trend_data,
                performance_data=None,
                matchup_context=None,
            )
            predictions_response = PredictionResponse(
                predicted_value_7d=prediction.predicted_value_7d,
                predicted_value_14d=prediction.predicted_value_14d,
                predicted_value_30d=prediction.predicted_value_30d,
                change_7d_pct=prediction.value_change_7d_pct,
                change_14d_pct=prediction.value_change_14d_pct,
                change_30d_pct=prediction.value_change_30d_pct,
                confidence=prediction.prediction_confidence,
                form_trajectory=prediction.form_trajectory,
            )
        except Exception as e:
            logger.warning(f"EnhancedAnalyzer.predict_player_value failed: {e}")

        # 6. Run RiskAnalyzer for risk metrics
        risk_metrics_response = None
        try:
            risk_analyzer = RiskAnalyzer()
            # Reverse price_history for risk analyzer (most recent first)
            price_history_reversed = list(reversed(price_history)) if price_history else []
            expected_return = predictions_response.change_30d_pct if predictions_response else 0.0
            risk_metrics = risk_analyzer.calculate_risk_metrics(
                player_info,
                price_history=price_history_reversed,
                performance_volatility=performance_volatility,
                expected_return_30d=expected_return,
            )
            risk_metrics_response = RiskMetricsResponse(
                price_volatility=risk_metrics.price_volatility,
                performance_volatility=risk_metrics.performance_volatility,
                volatility_score=risk_metrics.volatility_score,
                var_7d_pct=risk_metrics.var_7d_95pct,
                var_30d_pct=risk_metrics.var_30d_95pct,
                sharpe_ratio=risk_metrics.sharpe_ratio,
                expected_return_30d=risk_metrics.expected_return_30d,
                risk_category=risk_metrics.risk_category,
                confidence=risk_metrics.confidence,
            )
        except Exception as e:
            logger.warning(f"RiskAnalyzer.calculate_risk_metrics failed: {e}")

        # 7. Get matchup/schedule data
        schedule_response = None
        status_str = None
        lineup_prob = None
        try:
            # Get player details for matchup data
            player_details = api.client.get_player_details(league.id, player_id)
            if player_details:
                matchup_analyzer = MatchupAnalyzer()
                player_status = matchup_analyzer.analyze_player_status(player_details)
                status_str = player_status.reason
                lineup_prob = player_status.lineup_probability

                # Get upcoming matches - all remaining matches
                # mdst: 0 = not started, 1 = in progress, 2 = finished
                matchups_data = player_details.get("mdsum", [])
                # Include matches that haven't finished (status 0 or 1)
                upcoming_matches = [m for m in matchups_data if m.get("mdst", 2) < 2]

                if upcoming_matches:
                    upcoming_list = []
                    total_rank = 0
                    rank_count = 0
                    player_avg_points = player_info.average_points or 0

                    for match in upcoming_matches:  # All remaining matches
                        player_team_id = player_details.get("tid", "")
                        t1_id = match.get("t1", "")
                        t2_id = match.get("t2", "")
                        is_home = t1_id == player_team_id
                        opponent_id = t2_id if is_home else t1_id
                        matchday = match.get("mdid")

                        # Try to get opponent info
                        opponent_name = f"Team {opponent_id}"
                        opponent_rank = None
                        opponent_wins = None
                        opponent_draws = None
                        opponent_losses = None
                        opponent_points = None
                        opponent_strength = None
                        difficulty = "Medium"
                        analysis = None
                        expected_points = None

                        try:
                            opponent_profile = api.client.get_team_profile(league.id, opponent_id)
                            if opponent_profile:
                                opponent_name = opponent_profile.get("tn", opponent_name)
                                opponent_rank = opponent_profile.get("pl")
                                opponent_wins = opponent_profile.get("tw", 0)
                                opponent_draws = opponent_profile.get("td", 0)
                                opponent_losses = opponent_profile.get("tl", 0)
                                opponent_points = (opponent_wins * 3) + opponent_draws

                                # Calculate opponent strength (0-100)
                                if opponent_rank:
                                    total_rank += opponent_rank
                                    rank_count += 1
                                    opponent_strength = ((18 - opponent_rank) / 17) * 100

                                    # Determine difficulty
                                    if opponent_rank <= 5:
                                        difficulty = "Hard"
                                    elif opponent_rank >= 14:
                                        difficulty = "Easy"

                                    # Calculate expected points with home/away factor
                                    home_boost = 1.15 if is_home else 0.9
                                    difficulty_factor = 1.0
                                    if opponent_rank <= 3:
                                        difficulty_factor = 0.7
                                    elif opponent_rank <= 6:
                                        difficulty_factor = 0.85
                                    elif opponent_rank >= 15:
                                        difficulty_factor = 1.2
                                    elif opponent_rank >= 12:
                                        difficulty_factor = 1.1

                                    expected_points = round(
                                        player_avg_points * home_boost * difficulty_factor, 1
                                    )

                                    # Generate analysis text
                                    if difficulty == "Easy":
                                        if is_home:
                                            analysis = f"Favorable home match vs weak {opponent_name} (#{opponent_rank}). High scoring potential."
                                        else:
                                            analysis = f"Good away fixture vs struggling {opponent_name} (#{opponent_rank}). Above-average returns expected."
                                    elif difficulty == "Hard":
                                        if is_home:
                                            analysis = f"Tough home test vs top-6 {opponent_name} (#{opponent_rank}). May see reduced output."
                                        else:
                                            analysis = f"Difficult away trip to {opponent_name} (#{opponent_rank}). Expect below-average points."
                                    else:
                                        if is_home:
                                            analysis = f"Standard home fixture vs {opponent_name} (#{opponent_rank}). Normal returns expected."
                                        else:
                                            analysis = f"Routine away match at {opponent_name} (#{opponent_rank}). Moderate expectations."
                        except Exception as e:
                            logger.debug(f"Failed to get opponent profile: {e}")

                        upcoming_list.append(
                            MatchupResponse(
                                opponent=opponent_name,
                                opponent_rank=opponent_rank,
                                is_home=is_home,
                                date=match.get("md", ""),
                                difficulty=difficulty,
                                matchday=matchday,
                                opponent_wins=opponent_wins,
                                opponent_draws=opponent_draws,
                                opponent_losses=opponent_losses,
                                opponent_points=opponent_points,
                                opponent_strength=opponent_strength,
                                expected_points=expected_points,
                                analysis=analysis,
                            )
                        )

                    avg_rank = total_rank / rank_count if rank_count > 0 else 9.5
                    if avg_rank <= 6:
                        diff_rating = "Difficult"
                    elif avg_rank >= 13:
                        diff_rating = "Easy"
                    else:
                        diff_rating = "Medium"

                    schedule_response = ScheduleResponse(
                        upcoming=upcoming_list,
                        difficulty_rating=diff_rating,
                        avg_opponent_rank=avg_rank,
                    )
        except Exception as e:
            logger.warning(f"Schedule analysis failed: {e}")

        return PlayerFullResponse(
            id=player_info.id,
            first_name=player_info.first_name,
            last_name=player_info.last_name,
            position=player_info.position,
            team_name=player_info.team_name,
            team_id=player_info.team_id,
            market_value=player_info.market_value,
            price=market_price,
            points=player_info.points,
            average_points=player_info.average_points,
            games_played=games_played,
            status=status_str,
            lineup_probability=lineup_prob,
            value_score=analysis_value_score,
            recommendation=analysis_recommendation,
            confidence=analysis_confidence,
            factors=analysis_factors,
            factor_details=analysis_factor_details,
            trend_direction=trend_direction,
            trend_pct=trend_pct,
            trend_history=trend_history,
            predictions=predictions_response,
            risk_metrics=risk_metrics_response,
            schedule=schedule_response,
            is_on_market=is_on_market,
            is_in_squad=is_in_squad,
            roster_recommendation=roster_recommendation,
            roster_value_score=roster_value_score,
            roster_impact=roster_impact_str,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get full player detail: {str(e)}"
        ) from e


@router.get("/trends")
async def get_market_trends(current_user: TokenData = Depends(get_current_user)):
    """Get market trend summary"""
    try:
        api = get_cached_api(current_user.email)
        leagues = await run_sync(api.get_leagues)

        if not leagues:
            raise HTTPException(status_code=400, detail="No leagues found")

        league = leagues[0]
        market_players = await run_sync(api.get_market, league)

        total_value = sum(player.market_value for player in market_players)

        # Trend data not available via API - return basic stats
        return {
            "total_players": len(market_players),
            "total_value": total_value,
            "rising": 0,
            "falling": 0,
            "stable": len(market_players),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get trends: {str(e)}") from e
