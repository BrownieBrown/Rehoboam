#!/usr/bin/env python3
"""Test the /stats endpoint for market value history"""

import json

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings


def test_stats_endpoint():
    """Test the /leagues/{leagueId}/players/{playerId}/stats endpoint"""

    print("=" * 70)
    print("TESTING /stats ENDPOINT FOR MARKET VALUE HISTORY")
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

    print("\n3. Finding Rocco Reitz...")
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
        print("❌ Rocco Reitz not found, using first market player...")
        rocco = market[0] if market else None

    if not rocco:
        print("❌ No players found!")
        return

    print(f"✓ Testing with: {rocco.first_name} {rocco.last_name}")
    print(f"   Player ID: {rocco.id}")
    print(f"   Current Market Value: €{rocco.market_value:,}")

    print(f"\n{'=' * 70}")
    print("TESTING /stats ENDPOINT")
    print(f"{'=' * 70}")

    # Test the stats endpoint
    url = f"https://api.kickbase.com/v4/leagues/{league.id}/players/{rocco.id}/stats"
    print(f"\nURL: {url}")

    try:
        response = api.client.session.get(url)

        if response.status_code == 200:
            data = response.json()

            print(f"\n✅ Success! Status: {response.status_code}")
            print("\n📋 Raw Response:")
            print(json.dumps(data, indent=2, ensure_ascii=False))

            print(f"\n{'=' * 70}")
            print("FIELD ANALYSIS")
            print(f"{'=' * 70}")

            # Analyze top-level keys
            print("\n📋 Top-level keys:")
            for key in data.keys():
                value = data[key]
                value_type = type(value).__name__

                if isinstance(value, list):
                    print(f"   • '{key}' ({value_type}, length={len(value)})")
                    if len(value) > 0:
                        print(f"      First item: {value[0]}")
                elif isinstance(value, dict):
                    print(f"   • '{key}' ({value_type}, keys={list(value.keys())})")
                elif isinstance(value, int | float):
                    if value > 1000:
                        print(f"   • '{key}' ({value_type}) = €{value:,}")
                    else:
                        print(f"   • '{key}' ({value_type}) = {value}")
                else:
                    print(f"   • '{key}' ({value_type}) = {value}")

            # Check for marketValues array
            if "marketValues" in data:
                market_values = data["marketValues"]
                print("\n📈 Market Values Array:")
                print(f"   Found {len(market_values)} data points")

                if len(market_values) > 0:
                    print("\n   Sample Data Points:")
                    for i, item in enumerate(market_values[:5]):
                        print(f"      {i+1}. {item}")

                    if len(market_values) > 5:
                        print(f"      ... ({len(market_values) - 5} more)")
                        print(f"      {len(market_values)}. {market_values[-1]}")

                    # Try to calculate trend
                    print("\n   📊 Trend Analysis:")
                    if all("m" in item for item in market_values):
                        values = [item["m"] for item in market_values]
                        print(f"      First Value: €{values[0]:,}")
                        print(f"      Last Value: €{values[-1]:,}")

                        change = values[-1] - values[0]
                        if values[0] > 0:
                            change_pct = (change / values[0]) * 100
                            print(f"      Change: €{change:,} ({change_pct:+.1f}%)")

                            if change_pct > 5:
                                print("      Direction: 🟢 Rising")
                            elif change_pct < -5:
                                print("      Direction: 🔴 Falling")
                            else:
                                print("      Direction: ⚪ Stable")

                        # Calculate daily changes
                        print("\n   📅 Recent Daily Changes:")
                        for i in range(min(5, len(values) - 1)):
                            idx = -(i + 2)  # Start from second-to-last
                            daily_change = values[idx + 1] - values[idx]
                            daily_pct = (daily_change / values[idx]) * 100 if values[idx] > 0 else 0
                            print(f"      Day -{i+1}: €{daily_change:,} ({daily_pct:+.1f}%)")

            # Check for current market value
            if "marketValue" in data:
                print(f"\n💰 Current Market Value: €{data['marketValue']:,}")

            # Check for owner info
            if "leaguePlayer" in data:
                league_player = data["leaguePlayer"]
                print("\n👤 Owner Info:")
                if "userName" in league_player:
                    print(f"   Owner: {league_player['userName']}")
                else:
                    print("   Owner: KICKBASE (Computer)")
                print(f"   Full data: {league_player}")

            # Check for other useful fields
            print("\n🔍 Other Useful Fields:")
            for key in ["teamId", "position", "status", "points", "averagePoints"]:
                if key in data:
                    print(f"   {key}: {data[key]}")

        else:
            print(f"\n❌ Failed! Status: {response.status_code}")
            print(f"Response: {response.text}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()

    print(f"\n{'=' * 70}")
    print("COMPARISON WITH /marketvalue ENDPOINT")
    print(f"{'=' * 70}")

    # Compare with the other endpoint
    try:
        mv_response = api.client.get_player_market_value_history(
            league_id=league.id, player_id=rocco.id, timeframe=30
        )

        print("\n/marketvalue/30 response:")
        print(json.dumps(mv_response, indent=2, ensure_ascii=False))

    except Exception as e:
        print(f"\n❌ Could not fetch /marketvalue endpoint: {e}")


if __name__ == "__main__":
    test_stats_endpoint()
