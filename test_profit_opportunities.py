#!/usr/bin/env python3
"""Test profit opportunity detection with new market value endpoint"""

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings
from rehoboam.trader import Trader


def test_profit_opportunities():
    """Test that profit opportunities are now being detected"""

    print("=" * 70)
    print("TESTING PROFIT OPPORTUNITY DETECTION")
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

    print("\n3. Getting team info...")
    team_info = api.get_team_info(league)
    current_budget = team_info.get("budget", 0)
    team_value = team_info.get("team_value", 0)

    print(f"   Budget: €{current_budget:,}")
    print(f"   Team Value: €{team_value:,}")

    # Calculate debt capacity
    max_debt = int(team_value * 0.60)  # 60% of team value
    flip_budget = current_budget + max_debt
    print(f"   Max Debt (60%): €{max_debt:,}")
    print(f"   Flip Budget: €{flip_budget:,}")

    print(f"\n{'=' * 70}")
    print("TESTING NEW MARKET VALUE ENDPOINT")
    print(f"{'=' * 70}")

    # Get a sample of market players
    print("\n4. Fetching market players...")
    market = api.get_market(league)
    kickbase_market = [p for p in market if p.is_kickbase_seller()]

    print(f"   Total market players: {len(market)}")
    print(f"   KICKBASE-owned: {len(kickbase_market)}")

    # Test the new endpoint with a few players
    print("\n5. Testing market value history for first 5 KICKBASE players...")

    test_count = min(5, len(kickbase_market))
    for i, player in enumerate(kickbase_market[:test_count]):
        print(f"\n   Player {i+1}: {player.first_name} {player.last_name}")
        print(f"      ID: {player.id}")
        print(f"      Price: €{player.price:,}")
        print(f"      Market Value: €{player.market_value:,}")

        try:
            # Use new endpoint
            history = api.client.get_player_market_value_history_v2(
                player_id=player.id, timeframe=92
            )

            it_array = history.get("it", [])
            print(f"      Historical data points: {len(it_array)}")

            if it_array and len(it_array) >= 14:
                # Calculate 14-day trend
                recent = it_array[-14:]
                first_value = recent[0].get("mv", 0)
                last_value = recent[-1].get("mv", 0)

                if first_value > 0:
                    trend_pct = ((last_value - first_value) / first_value) * 100
                    print(f"      14-day trend: {trend_pct:+.1f}%")

                    if trend_pct > 5:
                        print("      Direction: 🟢 RISING")
                    elif trend_pct < -5:
                        print("      Direction: 🔴 FALLING")
                    else:
                        print("      Direction: ⚪ STABLE")

                    # Peak analysis
                    peak_value = history.get("hmv", 0)
                    low_value = history.get("lmv", 0)

                    print(f"      Peak value: €{peak_value:,}")
                    print(f"      Low value: €{low_value:,}")

                    if peak_value > 0:
                        vs_peak_pct = ((last_value - peak_value) / peak_value) * 100
                        print(f"      Current vs Peak: {vs_peak_pct:.1f}%")

                        if vs_peak_pct < -10:
                            print("      Status: ⚠️  UNDERVALUED (below peak)")

                    # Check if profitable
                    value_gap = player.market_value - player.price
                    value_gap_pct = (value_gap / player.price) * 100 if player.price > 0 else 0

                    print(f"      Value gap: €{value_gap:,} ({value_gap_pct:.1f}%)")

                    if value_gap_pct >= 10:
                        print("      ✅ PROFIT OPPORTUNITY!")
            else:
                print("      ⚠️  Insufficient data (need 14+ days)")

        except Exception as e:
            print(f"      ❌ Error: {e}")

    print(f"\n{'=' * 70}")
    print("TESTING PROFIT TRADER INTEGRATION")
    print(f"{'=' * 70}")

    # Now test the full Trader integration
    print("\n6. Running Trader.find_profit_opportunities()...")

    trader = Trader(api=api, settings=settings)

    try:
        opportunities = trader.find_profit_opportunities(league)

        print(f"\n✅ Found {len(opportunities)} profit opportunities!")

        if opportunities:
            print("\nTop 5 Opportunities:")
            for i, opp in enumerate(opportunities[:5]):
                print(f"\n   {i+1}. {opp.player.first_name} {opp.player.last_name}")
                print(f"      Buy Price: €{opp.buy_price:,}")
                print(f"      Market Value: €{opp.market_value:,}")
                print(f"      Profit Potential: €{opp.value_gap:,} ({opp.value_gap_pct:.1f}%)")
                print(f"      Expected Appreciation: {opp.expected_appreciation:.1f}%")
                print(f"      Risk Score: {opp.risk_score:.0f}/100")
                print(f"      Hold Days: {opp.hold_days}")
                print(f"      Reason: {opp.reason}")
        else:
            print("\n⚠️  No opportunities found - checking why...")
            print("   This might be due to:")
            print("   - No undervalued players (price < market value)")
            print("   - No players meet 10% profit threshold")
            print("   - All players too risky")

    except Exception as e:
        print(f"\n❌ Error in profit trader: {e}")
        import traceback

        traceback.print_exc()

    print(f"\n{'=' * 70}")
    print("TEST COMPLETE")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    test_profit_opportunities()
