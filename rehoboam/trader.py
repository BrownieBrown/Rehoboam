"""Trading logic and automation"""

from rich.console import Console
from rich.table import Table

from .analyzer import MarketAnalyzer, PlayerAnalysis
from .api import KickbaseAPI
from .bid_monitor import BidMonitor, ReplacementPlan
from .bidding_strategy import SmartBidding
from .config import Settings
from .historical_tracker import HistoricalTracker
from .kickbase_client import League
from .matchup_analyzer import MatchupAnalyzer
from .opportunity_cost import OpportunityCostAnalyzer
from .risk_analyzer import RiskAnalyzer, RiskMetrics
from .value_history import ValueHistoryCache
from .value_tracker import ValueTracker

console = Console()


class Trader:
    """Handles automated trading operations"""

    def __init__(
        self, api: KickbaseAPI, settings: Settings, verbose: bool = False, bid_learner=None
    ):
        self.api = api
        self.settings = settings
        self.verbose = verbose
        self.analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=settings.min_buy_value_increase_pct,
            min_sell_profit_pct=settings.min_sell_profit_pct,
            max_loss_pct=settings.max_loss_pct,
            min_value_score_to_buy=settings.min_value_score_to_buy,
        )
        self.history_cache = ValueHistoryCache()
        # Connect learner to bidding strategy for adaptive learning
        self.bidding = SmartBidding(bid_learner=bid_learner)
        self.bid_monitor = BidMonitor(api=api)
        self.matchup_analyzer = MatchupAnalyzer()
        self.risk_analyzer = RiskAnalyzer()
        self.value_tracker = ValueTracker()
        self.opportunity_cost_analyzer = OpportunityCostAnalyzer(
            min_squad_size=settings.min_squad_size
        )
        self.historical_tracker = HistoricalTracker()

    def analyze_market(
        self, league: League, calculate_risk: bool = False, track_history: bool = False
    ) -> list[PlayerAnalysis]:
        """
        Analyze all players on the market

        Args:
            league: League object
            calculate_risk: Whether to calculate risk metrics
            track_history: Whether to track recommendations for learning
        """
        market_players = self.api.get_market(league)

        # Filter for only KICKBASE sellers (not user listings)
        kickbase_players = [p for p in market_players if p.is_kickbase_seller()]

        analyses = []
        for player in kickbase_players:
            # CRITICAL FIX: Fetch real performance data with caching
            # Market endpoint shows points=0 but performance endpoint has actual stats
            performance_volatility = 0.5  # Default if not available
            try:
                # Check cache first
                perf_data = self.history_cache.get_cached_performance(
                    player_id=player.id, league_id=league.id, max_age_hours=6  # Cache for 6 hours
                )

                if not perf_data:
                    # Fetch from API
                    perf_data = self.api.client.get_player_performance(league.id, player.id)
                    # Cache it
                    self.history_cache.cache_performance(player.id, league.id, perf_data)

                real_points = self._extract_total_points(perf_data)
                if real_points > 0:
                    # Update player with real points
                    player.points = real_points

                # Extract performance volatility for risk calculation
                if calculate_risk:
                    from .value_calculator import PlayerValue

                    player_value = PlayerValue.calculate(player, performance_data=perf_data)
                    if player_value.consistency_score is not None:
                        # Convert consistency to volatility (inverse relationship)
                        performance_volatility = 1.0 - player_value.consistency_score

            except Exception:
                pass  # Silent failure for performance data

            # Fetch historical trend data with caching
            history_data = self._get_player_trend(league, player.id)

            # Analyze trend with current market value
            if history_data:
                trend_data = self.history_cache.get_trend_analysis(
                    history_data, player.market_value
                )
            else:
                trend_data = {"has_data": False, "trend": "unknown"}

            # Fetch matchup context (team strength, opponent, player status)
            matchup_context = self._get_matchup_context(league, player.id)

            # Analyze player with trend and matchup data
            analysis = self.analyzer.analyze_market_player(
                player, trend_data=trend_data, matchup_context=matchup_context
            )

            # Calculate risk metrics if enabled
            if calculate_risk:
                risk_metrics = self._calculate_risk_metrics(
                    player=player,
                    league=league,
                    performance_volatility=performance_volatility,
                    expected_return_30d=None,  # Could integrate with predictions later
                )
                analysis.risk_metrics = risk_metrics

            analyses.append(analysis)

        # Track recommendations for learning if enabled
        if track_history:
            for analysis in analyses:
                # Only track BUY and SELL recommendations
                if analysis.recommendation in ["BUY", "SELL"]:
                    try:
                        self.historical_tracker.record_recommendation(
                            player_analysis=analysis, league_id=league.id
                        )
                    except Exception:
                        pass  # Silent failure for tracking

        return analyses

    def _extract_total_points(self, performance_data: dict) -> int:
        """
        Extract total points from performance data

        Args:
            performance_data: Response from get_player_performance

        Returns:
            Total points across all matches in current season
        """
        total_points = 0

        # Performance data structure:
        # {
        #   "it": [  # Seasons/competitions
        #     {
        #       "ti": "2024/2025",  # Season
        #       "n": "Competition name",
        #       "ph": [  # Performance history (matches)
        #         {
        #           "p": 15,  # Points for this match
        #           "day": 30,
        #           ...
        #         }
        #       ]
        #     }
        #   ]
        # }

        try:
            # Get all seasons and use the most recent one (current season)
            seasons = performance_data.get("it", [])
            if not seasons:
                return 0

            # Sort by season to get the latest one
            # Season format is like "2024/2025" or "2025/2026"
            seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
            current_season = seasons_sorted[0] if seasons_sorted else None

            if current_season:
                matches = current_season.get("ph", [])
                # Sum all points from matches in current season
                for match in matches:
                    points = match.get("p", 0)
                    total_points += points

        except Exception:
            return 0

        return total_points

    def _get_player_trend(self, league: League, player_id: str, timeframe: int = 30) -> dict:
        """
        Get player trend data with caching

        Args:
            league: League object
            player_id: Player ID
            timeframe: Days to look back (default: 30)

        Returns:
            Trend analysis dict
        """
        # Check cache first
        cached = self.history_cache.get_cached_history(
            player_id=player_id,
            league_id=league.id,
            timeframe=timeframe,
            max_age_hours=24,  # Cache for 24 hours
        )

        if cached:
            # Need current market value - get from player
            return self.history_cache.get_trend_analysis(
                cached, current_market_value=0
            )  # Will be passed separately

        # Fetch from API
        try:
            history_data = self.api.client.get_player_market_value_history(
                league_id=league.id, player_id=player_id, timeframe=timeframe
            )

            # Cache the result
            self.history_cache.cache_history(
                player_id=player_id, league_id=league.id, timeframe=timeframe, data=history_data
            )

            return history_data  # Return raw data, will analyze with market value
        except Exception:
            return None  # Silent failure for trend data

    def _get_matchup_context(self, league: League, player_id: str) -> dict:
        """
        Get matchup context for player evaluation

        Args:
            league: League object
            player_id: Player ID

        Returns:
            dict with matchup analysis including team strength and opponent info
        """
        try:
            # Fetch player details (team, status, matchups)
            player_details = self.api.client.get_player_details(league.id, player_id)
            team_id = player_details.get("tid", "")

            if not team_id:
                return {"has_data": False}

            # Fetch player's team profile
            player_team_profile = self.api.client.get_team_profile(league.id, team_id)
            player_team = self.matchup_analyzer.get_team_strength(player_team_profile)

            # Get next matchup
            next_matchup = self.matchup_analyzer.get_next_matchup(player_details)

            # Fetch opponent team if available
            opponent_team = None
            if next_matchup and next_matchup.opponent_id:
                try:
                    opponent_profile = self.api.client.get_team_profile(
                        league.id, next_matchup.opponent_id
                    )
                    opponent_team = self.matchup_analyzer.get_team_strength(opponent_profile)
                    next_matchup.opponent_name = opponent_team.team_name
                    next_matchup.opponent_rank = opponent_team.league_position
                    next_matchup.difficulty_score = (
                        self.matchup_analyzer.calculate_matchup_difficulty(
                            player_team, opponent_team
                        )
                    )
                except Exception:
                    pass  # Opponent data not critical

            # Calculate matchup bonus
            matchup_bonus = self.matchup_analyzer.get_matchup_bonus(
                player_details, player_team, opponent_team
            )

            # Calculate strength of schedule (hybrid approach)
            def fetch_opponent_team(opponent_id: str):
                """Helper to fetch opponent team for SOS calculation"""
                # Check cache first
                if opponent_id in self.matchup_analyzer.team_cache:
                    return self.matchup_analyzer.team_cache[opponent_id]

                try:
                    opp_profile = self.api.client.get_team_profile(league.id, opponent_id)
                    return self.matchup_analyzer.get_team_strength(opp_profile)
                except Exception:
                    return None

            sos_analysis = self.matchup_analyzer.analyze_strength_of_schedule(
                player_details, player_team, fetch_opponent_team
            )

            return {
                "has_data": True,
                "player_team": player_team,
                "opponent_team": opponent_team,
                "next_matchup": next_matchup,
                "matchup_bonus": matchup_bonus,
                "player_status": matchup_bonus.get("player_status", "Unknown"),
                "sos": sos_analysis,
            }

        except Exception:
            return {"has_data": False}

    def _calculate_risk_metrics(
        self,
        player,
        league: League,
        performance_volatility: float,
        expected_return_30d: float | None = None,
    ) -> RiskMetrics | None:
        """
        Calculate risk metrics for a player with caching

        Args:
            player: Player object
            league: League object
            performance_volatility: Performance CV from PlayerValue
            expected_return_30d: Expected 30-day return from predictions

        Returns:
            RiskMetrics or None if insufficient data
        """
        try:
            # Check cache first (6 hour TTL)
            cached = self.value_tracker.get_cached_risk_metrics(player.id, max_age_hours=6)
            if cached:
                # Return cached metrics (reconstruct RiskMetrics object)
                return RiskMetrics(
                    player_id=player.id,
                    player_name=f"{player.first_name} {player.last_name}",
                    price_volatility=cached["price_volatility"],
                    performance_volatility=cached["performance_volatility"],
                    volatility_score=self.risk_analyzer._normalize_volatility_score(
                        cached["price_volatility"]
                    ),
                    var_7d_95pct=cached["var_7d"],
                    var_30d_95pct=cached["var_30d"],
                    expected_return_30d=expected_return_30d or 0.0,
                    sharpe_ratio=cached["sharpe_ratio"],
                    risk_category=self.risk_analyzer._assess_risk_category(
                        self.risk_analyzer._normalize_volatility_score(cached["price_volatility"]),
                        cached["var_30d"],
                    ),
                    price_std_dev=0.0,  # Not cached
                    data_points=0,  # Not cached
                    confidence=0.8,  # Assume good confidence for cached data
                )

            # Get price history from API (92-day history)
            history_data = self.api.client.get_player_market_value_history_v2(
                player_id=player.id, timeframe=92
            )

            # Extract price history
            price_history = self.risk_analyzer.extract_price_history_from_api_data(history_data)

            # Also record daily price for future analysis
            self.value_tracker.record_daily_price(player.id, league.id, player.market_value)

            # Calculate risk metrics
            risk_metrics = self.risk_analyzer.calculate_risk_metrics(
                player=player,
                price_history=price_history,
                performance_volatility=performance_volatility,
                expected_return_30d=expected_return_30d,
            )

            # Cache the results
            self.value_tracker.cache_risk_metrics(
                player_id=player.id,
                price_volatility=risk_metrics.price_volatility,
                performance_volatility=risk_metrics.performance_volatility,
                var_7d=risk_metrics.var_7d_95pct,
                var_30d=risk_metrics.var_30d_95pct,
                sharpe_ratio=risk_metrics.sharpe_ratio,
                data_quality="good" if risk_metrics.data_points >= 14 else "fair",
            )

            return risk_metrics

        except Exception as e:
            if self.verbose:
                console.print(
                    f"[dim]Risk calculation failed for {player.first_name} {player.last_name}: {e}[/dim]"
                )
            return None

    def find_best_replacement(
        self, owned_player, market_players: list, league: League
    ) -> dict | None:
        """
        Find the best replacement for an owned player on the market

        Returns:
            dict with:
                - player: The replacement player
                - analysis: PlayerAnalysis for the replacement
                - value_improvement: How much better the replacement is (value score difference)
        """
        if not market_players:
            return None

        # Filter market players by same position
        same_position = [p for p in market_players if p.position == owned_player.position]

        if not same_position:
            return None

        # Analyze all potential replacements
        replacements = []
        for market_player in same_position:
            try:
                # Quick analysis (no matchup context to save time)
                analysis = self.analyzer.analyze_player(market_player)
                replacements.append(
                    {
                        "player": market_player,
                        "analysis": analysis,
                        "value_score": analysis.player_value.value_score,
                    }
                )
            except Exception:
                continue

        if not replacements:
            return None

        # Find the best replacement by value score
        best_replacement = max(replacements, key=lambda x: x["value_score"])

        # Calculate owned player's value score
        from .value_calculator import PlayerValue

        owned_value = PlayerValue.calculate(owned_player)
        owned_score = owned_value.value_score

        # Calculate improvement
        improvement = best_replacement["value_score"] - owned_score

        return {
            "player": best_replacement["player"],
            "analysis": best_replacement["analysis"],
            "value_improvement": improvement,
            "owned_score": owned_score,
            "replacement_score": best_replacement["value_score"],
        }

    def _fetch_player_trends(self, players: list, limit: int = 50) -> dict:
        """
        Fetch market value trend data for a list of players

        Args:
            players: List of player objects
            limit: Maximum number of players to analyze

        Returns:
            Dict mapping player_id -> trend data
        """
        player_trends = {}
        errors = []

        for player in players[:limit]:
            try:
                # Use the new competition-based endpoint (v2) - much better data!
                history_data = self.api.client.get_player_market_value_history_v2(
                    player_id=player.id, timeframe=92  # 3 months of data
                )

                # Extract the "it" array with historical values
                it_array = history_data.get("it", [])

                if it_array and len(it_array) >= 14:  # Need at least 14 days
                    # Calculate recent trend (last 14 days)
                    recent = it_array[-14:]
                    first_value = recent[0].get("mv", 0)
                    last_value = recent[-1].get("mv", 0)

                    if first_value > 0:
                        trend_pct = ((last_value - first_value) / first_value) * 100

                        # Also check longer-term trend (30 days if available)
                        longer_term_falling = False
                        if len(it_array) >= 30:
                            month_ago_value = it_array[-30].get("mv", 0)
                            if month_ago_value > 0:
                                month_trend_pct = (
                                    (last_value - month_ago_value) / month_ago_value
                                ) * 100
                                # If down >5% over 30 days, consider it falling
                                if month_trend_pct < -5:
                                    longer_term_falling = True

                        if trend_pct > 5:
                            trend_direction = "rising"
                        elif trend_pct < -5 or longer_term_falling:
                            trend_direction = "falling"
                        else:
                            trend_direction = "stable"

                        # Get peak info from API
                        peak_value = history_data.get("hmv", 0)
                        low_value = history_data.get("lmv", 0)

                        player_trends[player.id] = {
                            "has_data": True,
                            "trend": trend_direction,
                            "trend_pct": trend_pct,
                            "peak_value": peak_value,
                            "low_value": low_value,
                            "current_value": last_value,
                        }
                    else:
                        player_trends[player.id] = {"has_data": False}
                else:
                    player_trends[player.id] = {"has_data": False}

            except Exception:
                player_trends[player.id] = {"has_data": False}

        return player_trends

    def find_profit_opportunities(self, league: League) -> list:
        """
        Find profit trading opportunities - Buy low, sell high

        Returns:
            List of ProfitOpportunity objects
        """
        from datetime import datetime

        from .profit_trader import ProfitTrader

        # Get market
        market = self.api.get_market(league)
        kickbase_market = [p for p in market if p.is_kickbase_seller()]

        # Get current budget and team value
        team_info = self.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)
        team_value = team_info.get("team_value", 0)

        # If team_value not available, calculate from squad
        if team_value == 0:
            squad = self.api.get_squad(league)
            team_value = sum(player.market_value for player in squad)

        # Calculate max debt allowed
        max_debt = int(team_value * (self.settings.max_debt_pct_of_team_value / 100))
        total_buying_power = current_budget + max_debt

        # Get next match date to ensure we're positive by gameday
        next_match_date = None
        days_until_match = None
        try:
            starting_eleven = self.api.get_starting_eleven(league)
            next_match = starting_eleven.get("nm") or starting_eleven.get("nextMatch")
            if next_match:
                if isinstance(next_match, int | float):
                    next_match_date = datetime.fromtimestamp(next_match)
                elif isinstance(next_match, str):
                    next_match_date = datetime.fromisoformat(next_match.replace("Z", "+00:00"))

                if next_match_date:
                    days_until_match = (next_match_date - datetime.now()).days
        except Exception:
            pass

        # Adjust flip budget based on days until match
        # If match is soon, be more conservative (need to sell flips before match)
        if days_until_match is not None:
            if days_until_match <= 2:
                # Match soon - only use positive budget
                flip_budget = max(0, current_budget)
                console.print(
                    f"[yellow]⚠️  Match in {days_until_match} days - using only positive budget for flips[/yellow]"
                )
            elif days_until_match <= 4:
                # Match approaching - use budget + 50% debt
                flip_budget = current_budget + int(max_debt * 0.5)
                console.print(
                    f"[cyan]Match in {days_until_match} days - conservative flip budget[/cyan]"
                )
            else:
                # Match far away - can use full debt capacity
                flip_budget = total_buying_power
                console.print(
                    f"[green]Match in {days_until_match} days - full flip budget available[/green]"
                )
        else:
            # No match date - use full capacity but be cautious
            flip_budget = current_budget + int(max_debt * 0.75)

        if current_budget < 0:
            pass

        # Get trend data for all market players
        player_trends = self._fetch_player_trends(kickbase_market, limit=50)

        # Find profit opportunities
        profit_trader = ProfitTrader(
            min_profit_pct=8.0,  # Need at least 8% profit potential (relaxed from 10%)
            max_hold_days=7,  # Hold max 7 days
            max_risk_score=60.0,  # Moderate-high risk tolerance (increased from 50)
        )

        # Find more opportunities when we have debt capacity
        max_opps = 5 if flip_budget < current_budget else 10

        opportunities = profit_trader.find_profit_opportunities(
            market_players=kickbase_market,
            current_budget=flip_budget,
            player_trends=player_trends,
            max_opportunities=max_opps,
        )

        return opportunities

    def find_trade_opportunities(self, league: League) -> list:
        """
        Find N-for-M trade opportunities that improve starting 11

        Returns:
            List of TradeRecommendation objects
        """
        from .trade_optimizer import TradeOptimizer
        from .value_calculator import PlayerValue

        # Get current squad and market
        squad = self.api.get_squad(league)
        market = self.api.get_market(league)

        # Filter to only KICKBASE-owned players (exclude human managers)
        kickbase_market = [p for p in market if p.is_kickbase_seller()]

        # Get current budget
        team_info = self.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)

        # Score market players (only affordable HEALTHY KICKBASE players)
        # Filter out injured players - we NEVER want injured players in lineup trades
        affordable_market = [
            p
            for p in kickbase_market
            if p.market_value <= current_budget and p.status == 0  # 0 = healthy
        ]

        # Fetch trend data for ALL players (squad + affordable market)
        all_players = list(squad) + affordable_market
        player_trends = self._fetch_player_trends(all_players, limit=len(all_players))

        # Calculate value scores for all players
        player_values = {}

        # Score squad players
        for player in squad:
            try:
                matchup_context = self._get_matchup_context(league, player.id)
                trend_data = player_trends.get(player.id)

                # Fetch performance data for sample size reliability
                perf_data = self.history_cache.get_cached_performance(
                    player_id=player.id, league_id=league.id, max_age_hours=6
                )
                if not perf_data:
                    try:
                        perf_data = self.api.client.get_player_performance(league.id, player.id)
                        self.history_cache.cache_performance(player.id, league.id, perf_data)
                    except Exception:
                        perf_data = None

                value = PlayerValue.calculate(
                    player, trend_data=trend_data, performance_data=perf_data
                )

                # Adjust for matchups/SOS
                if matchup_context and matchup_context.get("has_data"):
                    adjustment = 0
                    matchup_bonus_data = matchup_context.get("matchup_bonus", {})
                    adjustment += matchup_bonus_data.get("bonus_points", 0)

                    sos_data = matchup_context.get("sos")
                    if sos_data:
                        adjustment += sos_data.sos_bonus

                    value.value_score = max(0, min(100, value.value_score + adjustment))

                player_values[player.id] = value.value_score
            except Exception:
                player_values[player.id] = 0

        # Score market players (and filter risky ones based on games played)
        filtered_market = []
        for player in affordable_market:
            try:
                matchup_context = self._get_matchup_context(league, player.id)
                trend_data = player_trends.get(player.id)

                # Fetch performance data for sample size reliability
                perf_data = self.history_cache.get_cached_performance(
                    player_id=player.id, league_id=league.id, max_age_hours=6
                )
                if not perf_data:
                    try:
                        perf_data = self.api.client.get_player_performance(league.id, player.id)
                        self.history_cache.cache_performance(player.id, league.id, perf_data)
                    except Exception:
                        perf_data = None

                value = PlayerValue.calculate(
                    player, trend_data=trend_data, performance_data=perf_data
                )

                # FILTER: For lineup improvements, require minimum 5 games played
                # This prevents buying players like Emre Can (1 game, 100 pts) who are too risky
                # Also filter if we can't determine games played (no data = risky)
                if value.games_played is None or value.games_played < 5:
                    continue  # Skip players without sufficient game data

                if matchup_context and matchup_context.get("has_data"):
                    adjustment = 0
                    matchup_bonus_data = matchup_context.get("matchup_bonus", {})
                    adjustment += matchup_bonus_data.get("bonus_points", 0)

                    sos_data = matchup_context.get("sos")
                    if sos_data:
                        adjustment += sos_data.sos_bonus

                    value.value_score = max(0, min(100, value.value_score + adjustment))

                player_values[player.id] = value.value_score
                filtered_market.append(player)  # Only add if passed filters
            except Exception:
                player_values[player.id] = 0

        # Find best trades (using filtered market)
        optimizer = TradeOptimizer(
            max_players_out=3, max_players_in=3, bidding_strategy=self.bidding
        )
        trades = optimizer.find_best_trades(
            current_squad=squad,
            market_players=filtered_market,  # Use filtered list (no risky players)
            player_values=player_values,
            current_budget=current_budget,
            min_improvement_points=2.0,  # Need at least +2 pts/week improvement
            min_improvement_value=10.0,  # OR +10 value score improvement
        )

        return trades

    def analyze_team(self, league: League) -> list[PlayerAnalysis]:
        """Analyze all players in your team with comprehensive sell signals"""
        from datetime import datetime

        from .formation import select_best_eleven
        from .value_calculator import PlayerValue

        players = self.api.get_squad(league)

        # Fetch market trends and player stats for all squad players
        player_trends = self._fetch_player_trends(players, limit=len(players))

        # Fetch player statistics from API
        player_stats = {}
        for player in players:
            try:
                stats = self.api.client.get_player_statistics(player.id, league.id)
                player_stats[player.id] = stats
            except Exception:
                player_stats[player.id] = None

        # Get next match date to determine if we need to enforce squad size
        next_match_date = None
        days_until_match = None
        try:
            starting_eleven = self.api.get_starting_eleven(league)
            # Check for next match date in response
            # Common fields: "nextMatch", "nm", "matchDate", "md"
            next_match = starting_eleven.get("nm") or starting_eleven.get("nextMatch")
            if next_match:
                from datetime import datetime

                # Parse date (could be timestamp or ISO string)
                if isinstance(next_match, int | float):
                    next_match_date = datetime.fromtimestamp(next_match)
                elif isinstance(next_match, str):
                    next_match_date = datetime.fromisoformat(next_match.replace("Z", "+00:00"))

                if next_match_date:
                    days_until_match = (next_match_date - datetime.now()).days
        except Exception:
            pass

        # Only warn about squad size if match is approaching (within 2 days)
        enforce_squad_size = False
        if days_until_match is not None and days_until_match <= 2:
            enforce_squad_size = True
            if len(players) <= self.settings.min_squad_size:
                console.print(
                    f"[red]⚠️  Match in {days_until_match} days! Squad at minimum size ({len(players)}/{self.settings.min_squad_size})[/red]"
                )
        elif len(players) < self.settings.min_squad_size:
            console.print(
                f"[yellow]⚠️  Squad below minimum ({len(players)}/{self.settings.min_squad_size}). Consider buying before next match[/yellow]"
            )

        # STEP 1: Calculate preliminary value scores to determine best 11
        # (This prevents selling players just because they're bench for real team)
        player_values = {}
        for player in players:
            try:
                player_value = PlayerValue.calculate(player)
                player_values[player.id] = player_value.value_score
            except Exception:
                player_values[player.id] = 0

        # STEP 2: Determine best 11 based on value scores
        best_eleven = select_best_eleven(players, player_values)
        best_eleven_ids = {p.id for p in best_eleven}

        # STEP 3: Analyze each player with best 11 context
        analyses = []
        for player in players:
            try:
                # Get trend data (already fetched)
                trend = player_trends.get(player.id, {})
                stats = player_stats.get(player.id)

                # Extract purchase price from API
                purchase_price = player.market_value  # Default
                if stats and stats.get("trp"):
                    # trp = transfer price (what we paid)
                    purchase_price = stats["trp"]

                # Build peak analysis from trend data
                peak_dict = None
                if trend.get("has_data"):
                    peak_value = trend.get("peak_value", 0)
                    current_value = trend.get("current_value", player.market_value)
                    trend_direction = trend.get("trend")
                    trend_pct = trend.get("trend_pct", 0)

                    if peak_value > 0:
                        decline_pct = ((current_value - peak_value) / peak_value) * 100

                        # Player is "declining" if:
                        # 1. More than 5% below peak AND currently falling (negative trend)
                        # OR
                        # 2. More than 20% below peak AND not rising strongly (< +10%)
                        is_declining = False
                        if decline_pct < -5:
                            if trend_direction == "falling":
                                # Below peak and currently falling = declining
                                is_declining = True
                            elif decline_pct < -20 and trend_pct < 10:
                                # Far below peak and not recovering strongly = declining
                                is_declining = True

                        peak_dict = {
                            "is_declining": is_declining,
                            "decline_from_peak_pct": abs(decline_pct),
                            "days_since_peak": None,  # Could calculate from it array if needed
                            "peak_value": peak_value,
                        }

                # Convert trend data to format expected by analyzer
                trend_data = None
                if trend.get("has_data"):
                    trend_data = {
                        "has_data": True,
                        "direction": trend.get("trend"),
                        "change_pct": trend.get("trend_pct", 0),
                    }

                # Get matchup context (includes SOS!)
                matchup_context = self._get_matchup_context(league, player.id)

                # Check if player is in best 11
                is_in_best_eleven = player.id in best_eleven_ids

                # Analyze the player with full context
                analysis = self.analyzer.analyze_owned_player(
                    player,
                    purchase_price=purchase_price,
                    trend_data=trend_data,
                    matchup_context=matchup_context,
                    peak_analysis=peak_dict,
                    is_in_best_eleven=is_in_best_eleven,
                )

                # Only override SELL if match is within 2 days AND squad is at minimum
                if analysis.recommendation == "SELL":
                    if enforce_squad_size and len(players) <= self.settings.min_squad_size:
                        analysis.recommendation = "HOLD"
                        analysis.reason = f"Match in {days_until_match} days - need {self.settings.min_squad_size} players (was: {analysis.reason})"
                        analysis.confidence *= 0.5  # Lower confidence for forced HOLD

                analyses.append(analysis)

            except Exception:
                continue

        return analyses

    def display_analysis(
        self,
        analyses: list[PlayerAnalysis],
        title: str = "Analysis",
        show_bids: bool = True,
        show_risk: bool = False,
    ):
        """Display analysis results in a nice table"""
        table = Table(title=title, show_header=True, header_style="bold magenta")
        table.add_column("Player", style="cyan", no_wrap=True)
        table.add_column("Position", style="blue")
        table.add_column("Price", justify="right", style="yellow")
        if show_bids:
            table.add_column("Smart Bid", justify="right", style="bright_yellow")
        table.add_column("Value Score", justify="right", style="magenta")
        table.add_column("Pts/M€", justify="right", style="green")
        table.add_column("Points", justify="right", style="green")
        if show_risk:
            table.add_column("Risk", justify="center", style="yellow")
            table.add_column("Sharpe", justify="right", style="cyan")
            table.add_column("VaR (7d)", justify="right", style="red")
        table.add_column("Recommendation", justify="center")
        table.add_column("Reason", style="dim")

        for analysis in analyses:
            player = analysis.player
            name = f"{player.first_name} {player.last_name}"

            # Color code value score
            if analysis.value_score >= 60:
                score_color = "green"
            elif analysis.value_score >= 40:
                score_color = "yellow"
            else:
                score_color = "red"
            score_str = f"[{score_color}]{analysis.value_score:.1f}[/{score_color}]"

            # Color code recommendation
            rec_color = {
                "BUY": "green",
                "SELL": "red",
                "HOLD": "yellow",
                "SKIP": "dim",
            }.get(analysis.recommendation, "white")
            rec_str = f"[{rec_color}]{analysis.recommendation}[/{rec_color}]"

            # Calculate smart bid for BUY recommendations
            smart_bid_str = ""
            if show_bids and analysis.recommendation == "BUY":
                bid_rec = self.bidding.calculate_bid(
                    asking_price=analysis.current_price,
                    market_value=analysis.market_value,
                    value_score=analysis.value_score,
                    confidence=analysis.confidence,
                )
                overbid_pct_color = "green" if bid_rec.overbid_pct >= 10 else "yellow"
                smart_bid_str = f"€{bid_rec.recommended_bid:,}\n[{overbid_pct_color}]+{bid_rec.overbid_pct:.1f}%[/{overbid_pct_color}]"
            elif show_bids:
                smart_bid_str = "-"

            row_data = [
                name,
                player.position,
                f"€{analysis.current_price:,}",
            ]

            if show_bids:
                row_data.append(smart_bid_str)

            row_data.extend(
                [
                    score_str,
                    f"{analysis.points_per_million:.1f}",
                    str(analysis.points),
                ]
            )

            # Add risk columns if enabled
            if show_risk and analysis.risk_metrics:
                rm = analysis.risk_metrics
                risk_color = self.risk_analyzer.get_risk_color(rm.risk_category)
                sharpe_color = self.risk_analyzer.get_sharpe_color(rm.sharpe_ratio)
                var_color = self.risk_analyzer.get_var_color(rm.var_7d_95pct)

                risk_label = rm.risk_category.replace(" Risk", "")
                row_data.extend(
                    [
                        f"[{risk_color}]{risk_label}[/{risk_color}]",
                        f"[{sharpe_color}]{rm.sharpe_ratio:.2f}[/{sharpe_color}]",
                        f"[{var_color}]{rm.var_7d_95pct:.1f}%[/{var_color}]",
                    ]
                )
            elif show_risk:
                row_data.extend(["-", "-", "-"])

            row_data.extend([rec_str, analysis.reason])

            table.add_row(*row_data)

        console.print(table)

    def display_sell_analysis(
        self,
        analyses: list[PlayerAnalysis],
        title: str = "Sell Analysis",
        league: League | None = None,
    ):
        """Display sell recommendations with profit/loss and peak info"""

        table = Table(title=title, show_header=True, header_style="bold magenta")
        table.add_column("Player", style="cyan", no_wrap=True)
        table.add_column("Position", style="blue")
        table.add_column("Purchase", justify="right", style="dim")
        table.add_column("Current", justify="right", style="yellow")
        table.add_column("Peak", justify="right", style="bright_green")
        table.add_column("Profit/Loss", justify="right")
        table.add_column("Value Score", justify="right", style="magenta")
        table.add_column("Trend", justify="center", style="blue")
        table.add_column("Recommendation", justify="center")
        table.add_column("Reason", style="dim")

        for analysis in analyses:
            player = analysis.player
            name = f"{player.first_name} {player.last_name}"

            # Get purchase and peak info
            purchase_price = (
                analysis.current_price
            )  # This is purchase price from analyze_owned_player
            current_value = analysis.market_value
            profit_loss_pct = analysis.value_change_pct

            # Get peak info from metadata (populated by analyze_team)
            peak_str = "-"
            if (
                hasattr(analysis, "metadata")
                and analysis.metadata
                and "peak_value" in analysis.metadata
            ):
                peak_value = analysis.metadata["peak_value"]
                decline_pct = analysis.metadata.get("decline_from_peak_pct", 0)
                if decline_pct > 1:
                    # Declined from peak
                    peak_str = f"€{peak_value:,}\n[red]-{decline_pct:.1f}%[/red]"
                else:
                    # At or near peak
                    peak_str = f"€{peak_value:,}\n[green]at peak[/green]"

            # Color code profit/loss
            if profit_loss_pct > 20:
                pl_color = "bright_green"
            elif profit_loss_pct > 0:
                pl_color = "green"
            elif profit_loss_pct > -10:
                pl_color = "yellow"
            else:
                pl_color = "red"
            profit_loss_str = f"[{pl_color}]{profit_loss_pct:+.1f}%[/{pl_color}]"

            # Color code value score
            if analysis.value_score >= 60:
                score_color = "green"
            elif analysis.value_score >= 40:
                score_color = "yellow"
            else:
                score_color = "red"
            score_str = f"[{score_color}]{analysis.value_score:.1f}[/{score_color}]"

            # Trend indicator
            trend_str = "-"
            if analysis.trend:
                if "rising" in analysis.trend:
                    trend_str = f"[green]↗ {analysis.trend_change_pct:+.1f}%[/green]"
                elif "falling" in analysis.trend:
                    trend_str = f"[red]↘ {analysis.trend_change_pct:+.1f}%[/red]"
                elif "stable" in analysis.trend:
                    trend_str = f"[yellow]→ {analysis.trend_change_pct:+.1f}%[/yellow]"

            # Color code recommendation
            rec_color = {
                "SELL": "red",
                "HOLD": "yellow",
            }.get(analysis.recommendation, "white")
            rec_str = f"[{rec_color}]{analysis.recommendation}[/{rec_color}]"

            table.add_row(
                name,
                player.position,
                f"€{purchase_price:,}",
                f"€{current_value:,}",
                peak_str,
                profit_loss_str,
                score_str,
                trend_str,
                rec_str,
                analysis.reason,
            )

        console.print(table)

    def display_opportunity_costs(
        self,
        analyses: list[PlayerAnalysis],
        league: League,
        max_opportunities: int = 3,
    ):
        """
        Display opportunity cost analysis for top BUY recommendations

        Args:
            analyses: List of PlayerAnalysis objects (should be BUY recommendations)
            league: League object
            max_opportunities: Maximum number of opportunities to analyze
        """
        # Filter to BUY recommendations only
        buy_recommendations = [a for a in analyses if a.recommendation == "BUY"]

        if not buy_recommendations:
            return

        # Get current squad and budget
        try:
            squad = self.api.get_squad(league)
            team_info = self.api.get_team_info(league)
            current_budget = team_info.get("budget", 0)
        except Exception as e:
            console.print(
                f"[yellow]Could not fetch squad/budget for opportunity cost analysis: {e}[/yellow]"
            )
            return

        # Analyze squad to get value scores
        squad_analyses = self.analyze_team(league)

        # Display opportunity costs for top N BUY recommendations
        for analysis in buy_recommendations[:max_opportunities]:
            try:
                # Calculate opportunity cost
                cost_analysis = self.opportunity_cost_analyzer.analyze_buy_impact(
                    target_analysis=analysis,
                    current_squad=squad,
                    squad_analyses=squad_analyses,
                    current_budget=current_budget,
                )

                if cost_analysis:
                    # Display the opportunity cost panel
                    self.opportunity_cost_analyzer.display_opportunity_cost(
                        analysis=analysis, cost=cost_analysis
                    )

            except Exception as e:
                if self.verbose:
                    console.print(
                        f"[dim]Failed to calculate opportunity cost for {analysis.player.first_name} {analysis.player.last_name}: {e}[/dim]"
                    )

    def display_profit_opportunities(
        self,
        opportunities: list,
        title: str = "💰 Profit Trading Opportunities",
        current_budget: int = 0,
    ):
        """Display profit trading opportunities"""
        from rich.table import Table

        if not opportunities:
            console.print("[yellow]No profit opportunities found[/yellow]")
            return

        console.print(f"\n[bold magenta]{title}[/bold magenta]")
        console.print(
            "[dim]Buy undervalued players and flip for profit (can go into debt, sell before gameday)[/dim]\n"
        )

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Player", style="cyan")
        table.add_column("Position", style="blue")
        table.add_column("Buy Price", justify="right", style="yellow")
        table.add_column("Market Value", justify="right", style="green")
        table.add_column("Profit Pot.", justify="right", style="green")
        table.add_column("Hold Days", justify="center")
        table.add_column("Risk", justify="center")
        table.add_column("Reason", style="dim")

        for opp in opportunities:
            player = opp.player
            name = f"{player.first_name} {player.last_name}"

            # Color code profit potential
            profit_color = "green" if opp.value_gap_pct > 20 else "yellow"
            profit_str = (
                f"[{profit_color}]€{opp.value_gap:,}\n({opp.value_gap_pct:.1f}%)[/{profit_color}]"
            )

            # Color code risk
            if opp.risk_score < 30:
                risk_color = "green"
                risk_label = "Low"
            elif opp.risk_score < 60:
                risk_color = "yellow"
                risk_label = "Med"
            else:
                risk_color = "red"
                risk_label = "High"

            risk_str = f"[{risk_color}]{risk_label}[/{risk_color}]"

            table.add_row(
                name,
                player.position,
                f"€{opp.buy_price:,}",
                f"€{opp.market_value:,}",
                profit_str,
                f"{opp.hold_days}d",
                risk_str,
                opp.reason,
            )

        console.print(table)

        # Calculate total investment needed
        total_investment = sum(opp.buy_price for opp in opportunities)
        total_profit_potential = sum(opp.value_gap for opp in opportunities)
        avg_profit_pct = (
            sum(opp.value_gap_pct for opp in opportunities) / len(opportunities)
            if opportunities
            else 0
        )

        console.print(f"\n[bold]Total Investment:[/bold] €{total_investment:,}")
        console.print(
            f"[bold]Total Profit Potential:[/bold] €{total_profit_potential:,} (avg {avg_profit_pct:.1f}%)"
        )

        if current_budget > 0 and total_investment > current_budget:
            debt_needed = total_investment - current_budget
            console.print(f"[yellow]Debt Needed:[/yellow] €{debt_needed:,}")
            console.print(f"[dim]→ After buying: Budget = -€{debt_needed:,}[/dim]")
            console.print(
                f"[dim]→ After selling: Budget = €{total_profit_potential:,} profit[/dim]"
            )

        console.print(
            "\n[dim]Strategy: Buy these players, hold 3-7 days, sell when value increases, be positive by gameday[/dim]"
        )

    def display_trade_recommendations(self, trades: list, title: str = "💡 Recommended Trades"):
        """Display N-for-M trade recommendations"""

        if not trades:
            console.print("[yellow]No beneficial trades found[/yellow]")
            return

        console.print(f"\n[bold magenta]{title}[/bold magenta]")
        console.print(f"[dim]Found {len(trades)} trade(s) that improve your starting 11[/dim]")
        console.print(
            "[dim]Note: Only players with 5+ games played are considered for lineup improvements[/dim]\n"
        )

        for idx, trade in enumerate(trades[:5], 1):  # Show top 5 trades
            console.print(
                f"[bold cyan]Trade #{idx}: {trade.strategy.upper()} ({len(trade.players_out)}-for-{len(trade.players_in)})[/bold cyan]"
            )

            # Sell section
            console.print("[red]SELL:[/red]")
            for player in trade.players_out:
                console.print(
                    f"  • {player.first_name} {player.last_name} ({player.position}) - €{player.market_value:,}"
                )

            # Buy section
            console.print("[green]BUY:[/green]")
            for player in trade.players_in:
                # Show average points for context
                avg_points_str = (
                    f" | {player.average_points:.1f} pts/game"
                    if hasattr(player, "average_points")
                    else ""
                )
                console.print(
                    f"  • {player.first_name} {player.last_name} ({player.position}) - €{player.market_value:,}{avg_points_str}"
                )

            # Financial summary
            net_cost_color = "red" if trade.net_cost > 0 else "green"
            console.print("\n[bold]Financial Summary:[/bold]")
            console.print(f"  Total Cost: €{trade.total_cost:,}")
            console.print(f"  Total Proceeds: €{trade.total_proceeds:,}")
            console.print(f"  Net Cost: [{net_cost_color}]€{trade.net_cost:,}[/{net_cost_color}]")
            console.print(
                f"  [yellow]Required Budget: €{trade.required_budget:,}[/yellow] (buy first!)"
            )

            # Improvement summary
            console.print("\n[bold]Expected Improvement:[/bold]")
            console.print(f"  Points/Week: [green]+{trade.improvement_points:.1f}[/green]")
            console.print(f"  Value Score: [green]+{trade.improvement_value:.1f}[/green]")

            console.print("")  # Blank line between trades

    def execute_trades(self, league: League, buy_analyses: list[PlayerAnalysis]) -> dict:
        """Execute recommended trades"""
        results = {"bought": [], "failed": [], "skipped": []}

        if not buy_analyses:
            console.print("[yellow]No trading opportunities found[/yellow]")
            return results

        # Get current budget
        team_info = self.api.get_team_info(league)
        budget = team_info["budget"]
        team_value = team_info.get("team_value", 0)

        # If team_value not in API response, calculate from squad
        if team_value == 0:
            squad = self.api.get_squad(league)
            team_value = sum(player.market_value for player in squad)

        # Calculate available budget: can go negative up to max_debt_pct of team value
        max_debt = int(team_value * (self.settings.max_debt_pct_of_team_value / 100))
        available_budget = budget + max_debt - self.settings.reserve_budget

        console.print(f"\n[cyan]Team Value: €{team_value:,}[/cyan]")
        console.print(f"[cyan]Current Budget: €{budget:,}[/cyan]")
        console.print(
            f"[cyan]Max Allowable Debt: €{max_debt:,} ({self.settings.max_debt_pct_of_team_value}% of team value)[/cyan]"
        )
        console.print(f"[cyan]Available for trading: €{available_budget:,}[/cyan]\n")

        for analysis in buy_analyses:
            player = analysis.player

            # Find replacement candidates for this player
            replacement_candidates = self.find_replacement_candidates(
                league=league, target_player=analysis, current_budget=available_budget
            )

            # Determine if this is a replacement purchase
            is_replacement = len(replacement_candidates) > 0
            best_replacement = replacement_candidates[0] if is_replacement else None

            # Calculate smart bid
            bid_rec = self.bidding.calculate_bid(
                asking_price=analysis.current_price,
                market_value=analysis.market_value,
                value_score=analysis.value_score,
                confidence=analysis.confidence,
                is_replacement=is_replacement,
                replacement_sell_value=best_replacement["sell_value"] if best_replacement else 0,
            )

            # Check budget constraints
            if bid_rec.recommended_bid > available_budget:
                console.print(
                    f"[yellow]Skipping {player.first_name} {player.last_name}: "
                    f"Insufficient budget (€{bid_rec.recommended_bid:,} > €{available_budget:,})[/yellow]"
                )
                results["skipped"].append(analysis)
                continue

            if bid_rec.recommended_bid > self.settings.max_player_cost:
                console.print(
                    f"[yellow]Skipping {player.first_name} {player.last_name}: "
                    f"Exceeds max player cost (€{bid_rec.recommended_bid:,} > €{self.settings.max_player_cost:,})[/yellow]"
                )
                results["skipped"].append(analysis)
                continue

            # Execute trade with smart bid
            if self.settings.dry_run:
                console.print(
                    f"[blue][DRY RUN] Would bid €{bid_rec.recommended_bid:,} "
                    f"(+€{bid_rec.overbid_amount:,}, +{bid_rec.overbid_pct:.1f}%) for {player.first_name} {player.last_name}[/blue]"
                )
                console.print(f"[dim]  Strategy: {bid_rec.reasoning}[/dim]")
                results["bought"].append(analysis)
                available_budget -= bid_rec.recommended_bid
            else:
                try:
                    self.api.buy_player(league, player, bid_rec.recommended_bid)
                    console.print(
                        f"[green]✓ Bid €{bid_rec.recommended_bid:,} "
                        f"(+€{bid_rec.overbid_amount:,}) for {player.first_name} {player.last_name}[/green]"
                    )
                    console.print(f"[dim]  Strategy: {bid_rec.reasoning}[/dim]")

                    # Create replacement plan if applicable
                    replacement_plan = None
                    if best_replacement:
                        expected_budget = (
                            budget - bid_rec.recommended_bid + best_replacement["sell_value"]
                        )
                        replacement_plan = self.create_replacement_plan(
                            target_player=analysis,
                            replacement_candidate=best_replacement,
                            expected_budget_after=expected_budget,
                        )

                    # Register bid for monitoring with optional replacement plan
                    self.bid_monitor.register_bid(
                        player_id=player.id,
                        player_name=f"{player.first_name} {player.last_name}",
                        bid_amount=bid_rec.recommended_bid,
                        replacement_plan=replacement_plan,
                    )

                    results["bought"].append(analysis)
                    available_budget -= bid_rec.recommended_bid
                except Exception as e:
                    console.print(
                        f"[red]✗ Failed to make offer for {player.first_name} {player.last_name}: {e}[/red]"
                    )
                    results["failed"].append(analysis)

        return results

    def find_replacement_candidates(
        self, league: League, target_player: PlayerAnalysis, current_budget: int
    ) -> list[dict]:
        """
        Find players in current squad that could be replaced by target player

        Args:
            league: League
            target_player: Player we're considering buying
            current_budget: Current available budget

        Returns:
            List of replacement candidates with net cost and value improvement
        """
        # Get current squad
        squad_analyses = self.analyze_team(league)

        candidates = []

        for squad_player in squad_analyses:
            # Calculate metrics
            sell_value = squad_player.market_value
            buy_cost = target_player.current_price
            net_cost = buy_cost - sell_value

            # Check if replacement is affordable
            if net_cost > current_budget:
                continue

            # Calculate value improvement
            value_improvement = target_player.value_score - squad_player.value_score

            # Only consider if target is better
            if value_improvement <= 0:
                continue

            candidates.append(
                {
                    "squad_player": squad_player,
                    "sell_value": sell_value,
                    "buy_cost": buy_cost,
                    "net_cost": net_cost,
                    "value_improvement": value_improvement,
                    "points_change": target_player.points - squad_player.points,
                }
            )

        # Sort by value improvement (descending)
        candidates.sort(key=lambda c: c["value_improvement"], reverse=True)

        return candidates

    def create_replacement_plan(
        self, target_player: PlayerAnalysis, replacement_candidate: dict, expected_budget_after: int
    ) -> ReplacementPlan:
        """Create a replacement plan for bid monitor"""
        squad_player = replacement_candidate["squad_player"]
        player_obj = squad_player.player

        return ReplacementPlan(
            target_player_id=target_player.player.id,
            target_player_name=f"{target_player.player.first_name} {target_player.player.last_name}",
            players_to_sell=[
                {
                    "id": player_obj.id,
                    "name": f"{player_obj.first_name} {player_obj.last_name}",
                    "value": replacement_candidate["sell_value"],
                }
            ],
            net_profit=-replacement_candidate["net_cost"],  # Negative cost = profit
            expected_budget_after=expected_budget_after,
        )

    def optimize_squad_for_gameday(self, league: League):
        """
        Optimize squad for gameday - select best 11 and manage budget

        Returns:
            SquadOptimization result
        """
        from datetime import datetime

        from .squad_optimizer import SquadOptimizer
        from .value_calculator import PlayerValue

        # Get current squad
        squad = self.api.get_squad(league)

        # Get current budget and team info
        team_info = self.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)

        # Get next match date
        days_until_gameday = None
        try:
            starting_eleven = self.api.get_starting_eleven(league)
            next_match = starting_eleven.get("nm") or starting_eleven.get("nextMatch")
            if next_match:
                if isinstance(next_match, int | float):
                    next_match_date = datetime.fromtimestamp(next_match)
                elif isinstance(next_match, str):
                    next_match_date = datetime.fromisoformat(next_match.replace("Z", "+00:00"))

                if next_match_date:
                    days_until_gameday = (next_match_date - datetime.now()).days
        except Exception:
            pass

        # Calculate player values for all squad members
        player_values = {}
        for player in squad:
            try:
                # Get cached performance data
                perf_data = self.history_cache.get_cached_performance(
                    player_id=player.id, league_id=league.id, max_age_hours=6
                )
                if not perf_data:
                    try:
                        perf_data = self.api.client.get_player_performance(league.id, player.id)
                        self.history_cache.cache_performance(player.id, league.id, perf_data)
                    except Exception:
                        perf_data = None

                # Get matchup context (includes SOS)
                matchup_context = self._get_matchup_context(league, player.id)

                # Calculate value with all context
                value = PlayerValue.calculate(player, performance_data=perf_data)

                # Adjust for matchups/SOS
                if matchup_context and matchup_context.get("has_data"):
                    adjustment = 0
                    matchup_bonus_data = matchup_context.get("matchup_bonus", {})
                    adjustment += matchup_bonus_data.get("bonus_points", 0)

                    sos_data = matchup_context.get("sos")
                    if sos_data:
                        adjustment += sos_data.sos_bonus

                    value.value_score = max(0, min(100, value.value_score + adjustment))

                player_values[player.id] = value.value_score
            except Exception:
                player_values[player.id] = 0

        # Run optimization
        optimizer = SquadOptimizer(min_squad_size=self.settings.min_squad_size, max_squad_size=15)

        return optimizer.optimize_squad(
            squad=squad,
            player_values=player_values,
            current_budget=current_budget,
            days_until_gameday=days_until_gameday,
        )

    def get_player_values_from_analyses(self, analyses: list) -> dict[str, float]:
        """
        Extract player values from PlayerAnalysis list

        Args:
            analyses: List of PlayerAnalysis

        Returns:
            Dict mapping player.id -> value_score
        """
        return {a.player.id: a.value_score for a in analyses}

    def auto_trade(self, league: League, max_trades: int = 5):
        """Run automated trading cycle"""
        console.print("\n[bold cyan]Starting Automated Trading Cycle[/bold cyan]\n")

        if self.settings.dry_run:
            console.print("[yellow]⚠️  DRY RUN MODE - No real trades will be executed[/yellow]\n")

        # Analyze market
        market_analyses = self.analyze_market(league)

        # Find best opportunities
        opportunities = self.analyzer.find_best_opportunities(market_analyses, top_n=max_trades)

        if opportunities:
            console.print(f"\n[green]Found {len(opportunities)} trading opportunities![/green]\n")
            self.display_analysis(opportunities, title="Top Trading Opportunities")

            # Execute trades
            results = self.execute_trades(league, opportunities)

            # Summary
            console.print("\n[bold]Trading Summary:[/bold]")
            console.print(f"  Bought: {len(results['bought'])}")
            console.print(f"  Failed: {len(results['failed'])}")
            console.print(f"  Skipped: {len(results['skipped'])}")
        else:
            console.print("[yellow]No trading opportunities found at this time[/yellow]")
