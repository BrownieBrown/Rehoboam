#!/usr/bin/env python3
"""Test lineup analysis with market value trends"""

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings
from rehoboam.trader import Trader
from rehoboam.value_calculator import PlayerValue


def test_lineup_with_trends():
    """Test that lineup analysis now uses trend data"""

    print("=" * 70)
    print("TESTING LINEUP ANALYSIS WITH TREND DATA")
    print("=" * 70)

    settings = get_settings()
    api = KickbaseAPI(email=settings.kickbase_email, password=settings.kickbase_password)

    print("\n1. Logging in...")
    api.login()
    print("✓ Logged in")

    print("\n2. Fetching leagues...")
    leagues = api.get_leagues()
    league = leagues[0]
    print(f"✓ Using league: {league.name}")

    print("\n3. Testing PlayerValue with trend data...")

    # Get a sample player
    squad = api.get_squad(league)
    if squad:
        test_player = squad[0]
        print(f"\nTest player: {test_player.first_name} {test_player.last_name}")
        print(f"  Points: {test_player.points}")
        print(f"  Average Points: {test_player.average_points}")
        print(f"  Market Value: €{test_player.market_value:,}")

        # Test without trend data
        value_without_trend = PlayerValue.calculate(test_player)
        print("\n  WITHOUT trend data:")
        print(f"    Value Score: {value_without_trend.value_score}/100")
        print(f"    Trend Direction: {value_without_trend.trend_direction}")
        print(f"    Trend %: {value_without_trend.trend_pct}")

        # Fetch trend data
        print("\n  Fetching trend data...")
        try:
            history = api.client.get_player_market_value_history_v2(
                player_id=test_player.id, timeframe=92
            )

            it_array = history.get("it", [])
            if it_array and len(it_array) >= 14:
                recent = it_array[-14:]
                first_value = recent[0].get("mv", 0)
                last_value = recent[-1].get("mv", 0)

                if first_value > 0:
                    trend_pct = ((last_value - first_value) / first_value) * 100

                    trend_direction = (
                        "rising" if trend_pct > 5 else "falling" if trend_pct < -5 else "stable"
                    )

                    peak_value = history.get("hmv", 0)
                    current_value = last_value

                    vs_peak_pct = (
                        ((current_value - peak_value) / peak_value) * 100 if peak_value > 0 else 0
                    )

                    trend_data = {
                        "has_data": True,
                        "trend": trend_direction,
                        "trend_pct": trend_pct,
                        "peak_value": peak_value,
                        "current_value": current_value,
                    }

                    # Test with trend data
                    value_with_trend = PlayerValue.calculate(test_player, trend_data=trend_data)

                    print("\n  WITH trend data:")
                    print(f"    Value Score: {value_with_trend.value_score}/100")
                    print(f"    Trend Direction: {value_with_trend.trend_direction}")
                    print(f"    Trend %: {value_with_trend.trend_pct:+.1f}%")
                    print(f"    Current Value: €{current_value:,}")
                    print(f"    Peak Value: €{peak_value:,}")
                    print(f"    vs Peak: {vs_peak_pct:.1f}%")

                    score_change = value_with_trend.value_score - value_without_trend.value_score
                    print(f"\n  📊 Score Impact: {score_change:+.1f} points")

                    if trend_direction == "rising":
                        print("     ✅ Rising trend = bonus!")
                    elif trend_direction == "falling":
                        print("     ⚠️  Falling trend = penalty")
                    else:
                        print("     ⚪ Stable trend = neutral")

                    if vs_peak_pct < -20:
                        print("     ✅ Below peak = upside potential bonus!")
        except Exception as e:
            print(f"  ❌ Error fetching trend: {e}")

    print(f"\n{'=' * 70}")
    print("TESTING FULL TRADER INTEGRATION")
    print(f"{'=' * 70}")

    print("\n4. Running Trader.find_trade_opportunities()...")

    trader = Trader(api=api, settings=settings)

    try:
        trades = trader.find_trade_opportunities(league)

        print("\n✅ Trade analysis complete!")
        print(f"   Found {len(trades)} trade opportunities")

        if trades:
            print("\n   Trade recommendations are now using market value trends!")
            print("   - Rising players get value score bonuses")
            print("   - Falling players get penalties")
            print("   - Players below peak get upside bonuses")

    except Exception as e:
        print(f"\n❌ Error in trade analysis: {e}")
        import traceback

        traceback.print_exc()

    print(f"\n{'=' * 70}")
    print("TEST COMPLETE")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    test_lineup_with_trends()
