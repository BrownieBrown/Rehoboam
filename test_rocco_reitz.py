#!/usr/bin/env python3
"""Test market value history for Rocco Reitz"""

import json
from datetime import datetime

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings


def test_rocco_reitz():
    """Test fetching Rocco Reitz market value history"""

    print("=" * 70)
    print("TESTING ROCCO REITZ MARKET VALUE HISTORY")
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

    print("\n3. Finding Rocco Reitz on market...")
    market = api.get_market(league)

    rocco = None
    for player in market:
        if "reitz" in f"{player.first_name} {player.last_name}".lower():
            rocco = player
            break

    if not rocco:
        print("❌ Rocco Reitz not on market, checking squad...")
        squad = api.get_squad(league)
        for player in squad:
            if "reitz" in f"{player.first_name} {player.last_name}".lower():
                rocco = player
                break

    if not rocco:
        print("❌ Rocco Reitz not found!")
        return

    print(f"✓ Found: {rocco.first_name} {rocco.last_name}")
    print(f"   Player ID: {rocco.id}")
    print(f"   Current Market Value: €{rocco.market_value:,}")

    # Test different timeframes
    for timeframe in [30, 90, 180]:
        print(f"\n{'=' * 70}")
        print(f"FETCHING {timeframe}-DAY MARKET VALUE HISTORY")
        print(f"{'=' * 70}")

        try:
            response = api.client.get_player_market_value_history(
                league_id=league.id, player_id=rocco.id, timeframe=timeframe
            )

            print("\n📋 Raw API Response:")
            print(json.dumps(response, indent=2, ensure_ascii=False))

            print("\n📊 Field Analysis:")

            # Transfer price
            trp = response.get("trp", 0)
            if trp:
                print(f"   Transfer Price (trp): €{trp:,}")
            else:
                print("   Transfer Price (trp): Not available")

            # Peak values
            hmv = response.get("hmv", 0)
            lmv = response.get("lmv", 0)
            if hmv:
                print(f"   Highest Value (hmv): €{hmv:,}")
                if rocco.market_value < hmv:
                    decline = ((rocco.market_value - hmv) / hmv) * 100
                    print(f"   → Currently {decline:.1f}% below peak")
            if lmv:
                print(f"   Lowest Value (lmv): €{lmv:,}")

            # Historical data
            it = response.get("it", [])
            if it and isinstance(it, list):
                print(f"\n   Historical Data (it): {len(it)} data points")

                if len(it) > 0:
                    print("\n   Sample Data Points:")
                    for i, item in enumerate(it[:5]):  # Show first 5
                        dt = item.get("dt", 0)
                        mv = item.get("mv", 0)

                        # Convert days since epoch to date
                        date = datetime.fromtimestamp(dt * 86400)
                        print(f"      {i+1}. Date: {date.strftime('%Y-%m-%d')}, Value: €{mv:,}")

                    if len(it) > 5:
                        print(f"      ... ({len(it) - 5} more)")

                        # Show last one
                        last = it[-1]
                        dt = last.get("dt", 0)
                        mv = last.get("mv", 0)
                        date = datetime.fromtimestamp(dt * 86400)
                        print(f"      {len(it)}. Date: {date.strftime('%Y-%m-%d')}, Value: €{mv:,}")

                # Calculate trend
                if len(it) >= 2:
                    first_value = it[0].get("mv", 0)
                    last_value = it[-1].get("mv", 0)

                    if first_value > 0:
                        change = last_value - first_value
                        change_pct = (change / first_value) * 100

                        print("\n   📈 Trend Analysis:")
                        print(f"      First Value: €{first_value:,}")
                        print(f"      Last Value: €{last_value:,}")
                        print(f"      Change: €{change:,} ({change_pct:+.1f}%)")

                        if change_pct > 5:
                            print("      Direction: 🟢 Rising")
                        elif change_pct < -5:
                            print("      Direction: 🔴 Falling")
                        else:
                            print("      Direction: ⚪ Stable")

                # Check specific dates mentioned by user
                print("\n   Looking for value around 17.10 (October 17)...")
                october_17 = datetime(2024, 10, 17)
                october_17_days = int(october_17.timestamp() / 86400)

                for item in it:
                    dt = item.get("dt", 0)
                    mv = item.get("mv", 0)

                    if abs(dt - october_17_days) <= 1:  # Within 1 day
                        date = datetime.fromtimestamp(dt * 86400)
                        print(f"      Found: {date.strftime('%Y-%m-%d')}, Value: €{mv:,}")

            else:
                print("   ❌ No historical data in 'it' array")

            print("\n   Other Fields:")
            for key, value in response.items():
                if key not in ["trp", "hmv", "lmv", "it"]:
                    print(f"      {key}: {value}")

        except Exception as e:
            print(f"\n❌ Error fetching {timeframe}-day history: {e}")
            import traceback

            traceback.print_exc()

    print(f"\n{'=' * 70}")
    print("PROFIT OPPORTUNITY ANALYSIS")
    print(f"{'=' * 70}")

    # Based on user's info: €12M (17.10) → €14M (now), peak €19M
    print("\nUser's Observation:")
    print("   Started (17.10): ~€12,000,000")
    print("   Current: ~€14,000,000")
    print("   Peak: ~€19,000,000")
    print("   Trend: Rising every day")

    print("\nBot Should Detect:")
    print("   ✅ Rising trend (+16.7% from 17.10)")
    print("   ✅ Below peak (-26.3% from €19M)")
    print("   ✅ Profit opportunity: Buy now, sell near peak")
    print("   ✅ Undervalued: Has room to grow back to €19M")


if __name__ == "__main__":
    test_rocco_reitz()
