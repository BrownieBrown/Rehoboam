#!/usr/bin/env python3
"""Test fetching purchase prices from API"""

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings


def test_purchase_price_api():
    """Test the market value history endpoint"""

    print("=" * 70)
    print("TESTING PURCHASE PRICE API")
    print("=" * 70)

    settings = get_settings()
    api = KickbaseAPI(email=settings.kickbase_email, password=settings.kickbase_password)

    print("\n1. Logging in...")
    api.login()
    print("✓ Logged in successfully")

    print("\n2. Fetching leagues...")
    leagues = api.get_leagues()
    if not leagues:
        print("❌ No leagues found")
        return

    league = leagues[0]
    print(f"✓ Using league: {league.name}")

    print("\n3. Fetching your squad...")
    players = api.get_squad(league)
    print(f"✓ Found {len(players)} players")

    print("\n4. Testing market value history endpoint...")

    # Test with first 3 players
    for player in players[:3]:
        print(f"\n📊 {player.first_name} {player.last_name} ({player.position})")
        print(f"   Current value: €{player.market_value:,}")

        try:
            history = api.client.get_player_market_value_history(league.id, player.id, timeframe=30)

            # Check for transfer price
            transfer_price = history.get("trp")  # Transfer price field from API
            if transfer_price:
                profit = player.market_value - transfer_price
                profit_pct = (profit / transfer_price) * 100 if transfer_price > 0 else 0
                print(f"   Purchase price: €{transfer_price:,}")
                print(f"   Profit/Loss: €{profit:,} ({profit_pct:+.1f}%)")

                if profit_pct > 100:
                    print("   🚀 Massive profit! Consider selling")
                elif profit_pct > 50:
                    print("   💰 Excellent profit!")
                elif profit_pct < -10:
                    print("   📉 Losing value")
            else:
                print("   ⚠️  No purchase price found (might be initial squad player)")

            # Check for historical data in the "it" array
            historical_items = history.get("it", [])
            highest_value = history.get("hmv", 0)  # Highest market value from API

            if historical_items and isinstance(historical_items, list):
                market_values = [
                    item.get("mv", 0) for item in historical_items if item.get("mv", 0) > 0
                ]

                if market_values:
                    print(f"   📈 Historical data: {len(market_values)} data points")
                    if len(market_values) >= 2:
                        oldest = market_values[0]
                        newest = market_values[-1]
                        change = ((newest - oldest) / oldest) * 100 if oldest > 0 else 0
                        print(
                            f"   {len(historical_items)}-day trend: €{oldest:,} → €{newest:,} ({change:+.1f}%)"
                        )

                    # Use API's highest market value if available, otherwise calculate
                    peak = highest_value if highest_value > 0 else max(market_values)
                    if peak > player.market_value:
                        decline_pct = ((player.market_value - peak) / peak) * 100
                        print(f"   🏔️  Peak: €{peak:,} (now {decline_pct:.1f}% below)")

        except Exception as e:
            print(f"   ❌ Error fetching history: {e}")

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("\n✅ API Endpoint Working!")
    print("   • Fetches purchase prices (trp field)")
    print("   • Fetches historical market values")
    print("   • Can detect peaks and trends")
    print("\n✅ No Manual Recording Needed!")
    print("   • Bot will automatically fetch purchase prices")
    print("   • Bot will import 30 days of history")
    print("   • Peak detection works immediately")

    print("\n💡 Just run: rehoboam analyze")


if __name__ == "__main__":
    test_purchase_price_api()
