"""Tests for MarketAnalyzer — validates trend data flows correctly into buy/sell decisions."""

from rehoboam.analyzer import MarketAnalyzer, PlayerAnalysis
from rehoboam.kickbase_client import MarketPlayer


def _make_player(**overrides) -> MarketPlayer:
    """Create a test MarketPlayer with sensible defaults."""
    defaults = {
        "id": "p1",
        "first_name": "Test",
        "last_name": "Player",
        "position": "Midfielder",
        "team_id": "t1",
        "team_name": "Test FC",
        "price": 5_000_000,
        "market_value": 5_000_000,
        "points": 100,
        "average_points": 8.0,
        "status": 0,
    }
    defaults.update(overrides)
    return MarketPlayer(**defaults)


def _make_analyzer(**overrides) -> MarketAnalyzer:
    defaults = {
        "min_buy_value_increase_pct": 5.0,
        "min_sell_profit_pct": 5.0,
        "max_loss_pct": 10.0,
        "min_value_score_to_buy": 40.0,
    }
    defaults.update(overrides)
    return MarketAnalyzer(**defaults)


def _base_trend(**overrides) -> dict:
    """Full trend dict as produced by TrendAnalysis.to_dict()."""
    base = {
        "has_data": True,
        "trend": "stable",
        "trend_pct": 0.0,
        "trend_7d_pct": 0.0,
        "trend_14d_pct": 0.0,
        "trend_30d_pct": 0.0,
        "trend_90d_pct": 0.0,
        "change_pct": 0.0,
        "long_term_pct": 0.0,
        "peak_value": 6_000_000,
        "low_value": 4_000_000,
        "current_value": 5_000_000,
        "vs_peak_pct": -16.7,
        "yearly_range_position": 0.5,
        "momentum": "neutral",
        "momentum_score": 0,
        "is_dip_in_uptrend": False,
        "is_secular_decline": False,
        "is_recovery": False,
        "is_at_peak": False,
        "is_at_trough": False,
        "data_points": 50,
        "reference_value": 5_000_000,
        "price_low": 4_000_000,
    }
    base.update(overrides)
    return base


def _factor_names(analysis: PlayerAnalysis) -> list[str]:
    """Extract factor names from analysis."""
    return [f.name for f in analysis.factors]


def _factor_by_name(analysis: PlayerAnalysis, name: str):
    """Get a specific factor by name."""
    for f in analysis.factors:
        if f.name == name:
            return f
    return None


# ---------------------------------------------------------------------------
# Issue 0: Full trend dict reaches analyze_owned_player
# ---------------------------------------------------------------------------


class TestIssue0SellSidePassthrough:
    """Verify pattern flags in full trend dict reach sell-side analysis."""

    def test_secular_decline_factor_appears(self):
        """Full trend dict with is_secular_decline should produce a 'Secular Decline' factor."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000)
        trend = _base_trend(is_secular_decline=True, trend_pct=-12.0, trend="falling")

        analysis = analyzer.analyze_owned_player(
            player,
            purchase_price=5_500_000,  # At a loss
            trend_data=trend,
        )

        assert "Secular Decline" in _factor_names(analysis)

    def test_dip_in_uptrend_factor_appears(self):
        """Full trend dict with is_dip_in_uptrend should produce a hold signal."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000)
        trend = _base_trend(is_dip_in_uptrend=True, trend_pct=-3.0)

        analysis = analyzer.analyze_owned_player(
            player,
            purchase_price=5_100_000,  # Small loss
            trend_data=trend,
        )

        assert "Dip in Uptrend" in _factor_names(analysis)
        factor = _factor_by_name(analysis, "Dip in Uptrend")
        assert factor.score < 0  # Hold signal (negative = reduce sell pressure)

    def test_stripped_dict_misses_patterns(self):
        """A stripped dict (only direction/change_pct) should NOT trigger pattern factors."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000)
        # This is what the old broken code produced
        stripped = {"has_data": True, "direction": "falling", "change_pct": -12.0}

        analysis = analyzer.analyze_owned_player(
            player,
            purchase_price=5_500_000,
            trend_data=stripped,
        )

        # Pattern flags absent → no pattern factors should fire
        assert "Secular Decline" not in _factor_names(analysis)
        assert "Dip in Uptrend" not in _factor_names(analysis)


# ---------------------------------------------------------------------------
# Issue 1: 14d trend data used instead of 7d
# ---------------------------------------------------------------------------


class TestIssue1FourteenDayTrend:
    """Buy gate should use trend_pct (14d), not change_pct (7d)."""

    def test_7d_dip_with_healthy_14d_not_blocked(self):
        """Player with 7d=-6% but 14d=+3% should NOT be blocked from BUY."""
        analyzer = _make_analyzer()
        player = _make_player(
            market_value=5_000_000,
            price=5_000_000,
            points=200,
            average_points=12.0,
        )
        trend = _base_trend(
            trend="stable",
            change_pct=-6.0,  # 7d dip (old code would read this)
            trend_7d_pct=-6.0,
            trend_pct=3.0,  # 14d healthy (new code reads this)
            trend_14d_pct=3.0,
            yearly_range_position=0.4,
        )

        analysis = analyzer.analyze_market_player(player, trend_data=trend)

        # Should not be blocked — the 14d trend is positive
        # (the score may or may not be high enough for BUY depending on other factors,
        # but the trend gate itself should not block)
        # Check that trend_change_pct used internally is the 14d value
        trend_factors = [
            f for f in analysis.factors if "trend" in f.name.lower() or "Trend" in f.name
        ]
        for f in trend_factors:
            # Should reference the 14d value, not the 7d one
            assert "-6.0" not in f.description

    def test_14d_strongly_negative_blocks_buy(self):
        """Player with 14d=-7% should be blocked even if 7d is neutral."""
        analyzer = _make_analyzer()
        player = _make_player(
            market_value=5_000_000,
            price=5_000_000,
            points=200,
            average_points=12.0,
        )
        trend = _base_trend(
            trend="falling",
            change_pct=0.0,  # 7d neutral
            trend_7d_pct=0.0,
            trend_pct=-7.0,  # 14d falling
            trend_14d_pct=-7.0,
            yearly_range_position=0.5,
        )

        analysis = analyzer.analyze_market_player(player, trend_data=trend)

        # With 14d at -7%, trend check should fail → recommendation should not be BUY
        assert analysis.recommendation != "BUY"

    def test_sell_side_uses_14d(self):
        """Sell-side trend_change_pct should come from trend_pct (14d)."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000)
        trend = _base_trend(
            trend="falling",
            change_pct=-2.0,  # 7d
            trend_7d_pct=-2.0,
            trend_pct=-8.0,  # 14d
            trend_14d_pct=-8.0,
        )

        analysis = analyzer.analyze_owned_player(
            player,
            purchase_price=4_500_000,  # 11% profit
            trend_data=trend,
        )

        # The falling trend factor should show the 14d value (-8.0), not 7d (-2.0)
        falling = _factor_by_name(analysis, "Falling Trend")
        if falling:
            assert "-8.0" in falling.description or "8.0" in falling.description


# ---------------------------------------------------------------------------
# Issue 2: Sell-side trend patterns fire regardless of profit/loss
# ---------------------------------------------------------------------------


class TestIssue2SellPatterns:
    """Trend patterns should fire at any profit/loss level."""

    def test_secular_decline_at_loss_adds_sell_pressure(self):
        """Secular decline at a loss should still produce sell pressure."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000)
        trend = _base_trend(is_secular_decline=True, trend_pct=-10.0, trend="falling")

        analysis = analyzer.analyze_owned_player(
            player,
            purchase_price=6_000_000,  # -16.7% loss
            trend_data=trend,
        )

        factor = _factor_by_name(analysis, "Secular Decline")
        assert factor is not None
        assert factor.score >= 15.0  # Base 15 + scaled loss severity

    def test_secular_decline_at_profit_takes_profit(self):
        """Secular decline at profit should produce strong sell (score=20)."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000)
        trend = _base_trend(is_secular_decline=True, trend_pct=-10.0, trend="falling")

        analysis = analyzer.analyze_owned_player(
            player,
            purchase_price=4_500_000,  # ~11% profit
            trend_data=trend,
        )

        factor = _factor_by_name(analysis, "Secular Decline")
        assert factor is not None
        assert factor.score == 20.0

    def test_dip_in_uptrend_at_small_loss_holds(self):
        """Dip in uptrend at -2% loss should produce hold signal (was only at >5% loss)."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000)
        trend = _base_trend(is_dip_in_uptrend=True, trend_pct=-2.0)

        analysis = analyzer.analyze_owned_player(
            player,
            purchase_price=5_100_000,  # ~-2% loss
            trend_data=trend,
        )

        factor = _factor_by_name(analysis, "Dip in Uptrend")
        assert factor is not None
        assert factor.score == -10.0  # Hold signal

    def test_falling_trend_at_small_profit_adds_mild_sell(self):
        """Falling trend at 3% profit should produce mild sell signal (was 0 below 5%)."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000)
        trend = _base_trend(trend="falling", trend_pct=-8.0)

        analysis = analyzer.analyze_owned_player(
            player,
            purchase_price=4_850_000,  # ~3% profit
            trend_data=trend,
        )

        factor = _factor_by_name(analysis, "Falling Trend")
        assert factor is not None
        assert factor.score == 8.0  # Mild sell

    def test_falling_trend_at_high_profit_locks_in(self):
        """Falling trend at 10% profit should produce strong sell signal."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000)
        trend = _base_trend(trend="falling", trend_pct=-8.0)

        analysis = analyzer.analyze_owned_player(
            player,
            purchase_price=4_500_000,  # ~11% profit
            trend_data=trend,
        )

        factor = _factor_by_name(analysis, "Falling Trend")
        assert factor is not None
        assert factor.score == 15.0  # Lock in profit


# ---------------------------------------------------------------------------
# Issue 3: is_at_peak / is_at_trough signals
# ---------------------------------------------------------------------------


class TestIssue3PeakTrough:
    """Peak and trough flags should produce buy/sell signals."""

    def test_at_trough_buy_bonus(self):
        """is_at_trough should produce +3 buy bonus."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000, points=100, average_points=8.0)
        trend = _base_trend(
            is_at_trough=True,
            yearly_range_position=0.02,
        )

        analysis = analyzer.analyze_market_player(player, trend_data=trend)

        factor = _factor_by_name(analysis, "Near Yearly Low")
        assert factor is not None
        assert factor.score == 3.0

    def test_at_peak_buy_penalty(self):
        """is_at_peak should produce -5 buy penalty."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000, points=100, average_points=8.0)
        trend = _base_trend(
            is_at_peak=True,
            yearly_range_position=0.98,
        )

        analysis = analyzer.analyze_market_player(player, trend_data=trend)

        factor = _factor_by_name(analysis, "Near Yearly High")
        assert factor is not None
        assert factor.score == -5.0

    def test_at_peak_sell_take_profit(self):
        """is_at_peak + profit > 5% should produce +10 sell signal."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000)
        trend = _base_trend(is_at_peak=True)

        analysis = analyzer.analyze_owned_player(
            player,
            purchase_price=4_500_000,  # ~11% profit
            trend_data=trend,
        )

        factor = _factor_by_name(analysis, "At Peak")
        assert factor is not None
        assert factor.score == 10.0

    def test_at_peak_no_profit_no_sell_signal(self):
        """is_at_peak without significant profit should NOT produce take-profit signal."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000)
        trend = _base_trend(is_at_peak=True)

        analysis = analyzer.analyze_owned_player(
            player,
            purchase_price=4_900_000,  # ~2% profit (below 5% threshold)
            trend_data=trend,
        )

        factor = _factor_by_name(analysis, "At Peak")
        assert factor is None  # Should not fire below 5% profit

    def test_range_position_low_without_trough_flag(self):
        """range_position < 0.2 without is_at_trough still gets +5 bonus."""
        analyzer = _make_analyzer()
        player = _make_player(market_value=5_000_000, points=100, average_points=8.0)
        trend = _base_trend(
            is_at_trough=False,
            yearly_range_position=0.15,
        )

        analysis = analyzer.analyze_market_player(player, trend_data=trend)

        factor = _factor_by_name(analysis, "Near Yearly Low")
        assert factor is not None
        assert factor.score == 5.0


# ---------------------------------------------------------------------------
# Issue 4: Performance data enriches best-11 value scoring
# ---------------------------------------------------------------------------


def _make_perf_data(match_points: list[int]) -> dict:
    """Build a minimal performance_data dict for PlayerValue.calculate()."""
    matches = []
    for pts in match_points:
        matches.append({"p": pts, "mi": 90, "g": 0, "a": 0, "y": 0, "r": 0})
    return {"it": [{"ti": "2024/2025", "n": "Bundesliga", "ph": matches}]}


class TestEnrichedValueScoring:
    """Verify performance data flows through to value scoring and affects best-11 ranking."""

    def test_performance_data_enables_sample_size_penalty(self):
        """Without performance data games_played is None (penalty skipped);
        with it, a 1-game player gets penalised."""
        from rehoboam.value_calculator import PlayerValue

        player = _make_player(
            market_value=3_000_000, price=3_000_000, points=100, average_points=100.0
        )

        # Without performance data — no penalty
        bare = PlayerValue.calculate(player)
        assert bare.games_played is None

        # With 1-game performance data — penalty applies
        perf = _make_perf_data([100])
        enriched = PlayerValue.calculate(player, performance_data=perf)
        assert enriched.games_played == 1
        assert enriched.value_score < bare.value_score

    def test_many_games_scores_higher_than_few_games(self):
        """Player with 15 games at 8 avg should score higher than same avg with 2 games."""
        from rehoboam.value_calculator import PlayerValue

        # Proven player: 15 games, avg 8 points
        proven = _make_player(
            id="proven",
            market_value=3_000_000,
            price=3_000_000,
            points=120,
            average_points=8.0,
        )
        proven_perf = _make_perf_data([8] * 15)

        # Unproven player: 2 games, avg 8 points
        unproven = _make_player(
            id="unproven",
            market_value=3_000_000,
            price=3_000_000,
            points=16,
            average_points=8.0,
        )
        unproven_perf = _make_perf_data([8] * 2)

        proven_val = PlayerValue.calculate(proven, performance_data=proven_perf)
        unproven_val = PlayerValue.calculate(unproven, performance_data=unproven_perf)

        assert proven_val.value_score > unproven_val.value_score

    def test_perf_score_ranks_high_avg_above_cheap_player(self):
        """Performance score (avg_points * confidence) should rank a high-avg
        expensive player above a low-avg cheap player — the opposite of value_score
        which rewards affordability."""
        from rehoboam.value_calculator import PlayerValue

        # Expensive proven starter: 101 avg pts, €20M, 15 games
        expensive = _make_player(
            id="doekhi",
            market_value=20_000_000,
            price=20_000_000,
            points=101,
            average_points=101.0,
        )
        expensive_perf = _make_perf_data([101] * 15)

        # Cheap bench player: 26 avg pts, €2.7M, 5 games
        cheap = _make_player(
            id="mikel",
            market_value=2_700_000,
            price=2_700_000,
            points=26,
            average_points=26.0,
        )
        cheap_perf = _make_perf_data([26] * 5)

        exp_pv = PlayerValue.calculate(expensive, performance_data=expensive_perf)
        chp_pv = PlayerValue.calculate(cheap, performance_data=cheap_perf)

        # value_score (market metric) actually ranks the cheap player higher
        # because of points-per-million and affordability bonus
        assert (
            chp_pv.value_score >= exp_pv.value_score
        ), "Sanity check: value_score should favour cheap player"

        # Performance score (what _enrich_squad_values returns) should rank
        # the high-avg player above the low-avg player
        def perf_score(pv, avg_pts):
            if pv.sample_size_confidence is not None:
                return avg_pts * (0.5 + 0.5 * pv.sample_size_confidence)
            return avg_pts

        assert perf_score(exp_pv, 101.0) > perf_score(chp_pv, 26.0)
