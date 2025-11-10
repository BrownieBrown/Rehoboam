#!/usr/bin/env python3
"""Inspect the exact API response for Sinani's market value history"""

import json

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings


def inspect_api_response():
    """Fetch and display raw API response"""

    print("=" * 70)
    print("INSPECTING MARKET VALUE API RESPONSE")
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

    print("\n3. Fetching your squad to find Sinani...")
    players = api.get_squad(league)

    # Find Sinani
    sinani = None
    for player in players:
        if "sinani" in f"{player.first_name} {player.last_name}".lower():
            sinani = player
            break

    if not sinani:
        print("\n❌ Sinani not found in your squad")
        print("\n📋 Your squad:")
        for p in players[:10]:  # Show first 10
            print(f"   • {p.first_name} {p.last_name} ({p.position})")
        print("\nTrying with first player instead...")
        sinani = players[0]

    print(f"\n✓ Found: {sinani.first_name} {sinani.last_name}")
    print(f"   Player ID: {sinani.id}")
    print(f"   Current Value: €{sinani.market_value:,}")

    # Fetch market value history
    print("\n4. Fetching market value history (30 days)...")
    print(f"   URL: /v4/leagues/{league.id}/players/{sinani.id}/marketvalue/30")

    try:
        response = api.client.get_player_market_value_history(
            league_id=league.id, player_id=sinani.id, timeframe=30
        )

        print("\n" + "=" * 70)
        print("RAW API RESPONSE")
        print("=" * 70)
        print(json.dumps(response, indent=2, ensure_ascii=False))

        print("\n" + "=" * 70)
        print("FIELD ANALYSIS")
        print("=" * 70)

        # Analyze top-level fields
        print("\n📋 Top-level keys:")
        for key in response.keys():
            value = response[key]
            value_type = type(value).__name__

            if isinstance(value, list):
                print(f"   • '{key}' ({value_type}, length={len(value)})")
                if len(value) > 0:
                    print(f"      First item: {value[0]}")
                    print(f"      Last item: {value[-1]}")
            elif isinstance(value, int | float):
                print(f"   • '{key}' ({value_type}) = {value:,}")
            else:
                print(f"   • '{key}' ({value_type}) = {value}")

        print("\n" + "=" * 70)
        print("PURCHASE PRICE DETECTION")
        print("=" * 70)

        # Try different field names for purchase price
        purchase_fields = [
            "trp",
            "transferPrice",
            "tp",
            "buyPrice",
            "purchasePrice",
            "bp",
            "price",
            "p",
        ]
        found_purchase = False

        for field in purchase_fields:
            if field in response:
                value = response[field]
                print(f"\n✓ Found purchase price field: '{field}' = €{value:,}")

                if value and value > 0:
                    profit = sinani.market_value - value
                    profit_pct = (profit / value) * 100
                    print(f"   Current: €{sinani.market_value:,}")
                    print(f"   Purchased: €{value:,}")
                    print(f"   Profit: €{profit:,} ({profit_pct:+.1f}%)")
                    found_purchase = True
                else:
                    print(f"   ⚠️  Value is {value} (0 or null)")

        if not found_purchase:
            print(f"\n⚠️  No purchase price found. Tried fields: {', '.join(purchase_fields)}")
            print(f"   Available fields: {', '.join(response.keys())}")

        print("\n" + "=" * 70)
        print("HISTORICAL DATA DETECTION")
        print("=" * 70)

        # Check for historical data in "it" array
        historical_items = response.get("it", [])
        if historical_items and isinstance(historical_items, list):
            print(f"\n✓ Found historical data: 'it' array with {len(historical_items)} data points")

            if len(historical_items) > 0:
                first_item = historical_items[0]
                last_item = historical_items[-1]

                print("   Structure: {'dt': days_since_epoch, 'mv': market_value}")
                print(
                    f"   First item: dt={first_item.get('dt', 0)} (days), mv=€{first_item.get('mv', 0):,}"
                )
                print(
                    f"   Last item: dt={last_item.get('dt', 0)} (days), mv=€{last_item.get('mv', 0):,}"
                )

                # Extract all market values
                market_values = [
                    item.get("mv", 0) for item in historical_items if item.get("mv", 0) > 0
                ]
                if market_values:
                    print(f"   Range: €{min(market_values):,} - €{max(market_values):,}")

                    peak = max(market_values)
                    current = market_values[-1] if market_values else 0
                    if peak > current:
                        decline = ((current - peak) / peak) * 100
                        print(f"   🏔️  Peak: €{peak:,} (currently {decline:.1f}% below)")
        else:
            print("\n⚠️  No historical data found in 'it' array")

        # Check for pre-calculated peak values from API
        highest_value = response.get("hmv", 0)
        lowest_value = response.get("lmv", 0)

        if highest_value > 0:
            print(f"\n✓ Highest market value (hmv): €{highest_value:,}")
            if sinani.market_value < highest_value:
                decline = ((sinani.market_value - highest_value) / highest_value) * 100
                print(f"   Currently {decline:.1f}% below peak")

        if lowest_value > 0:
            print(f"\n✓ Lowest market value (lmv): €{lowest_value:,}")

        print("\n" + "=" * 70)
        print("RECOMMENDED FIELD MAPPING")
        print("=" * 70)

        print("\nCorrect field mapping for API response:")
        print("\n# Purchase Price (what you paid)")
        print("   transfer_price = response.get('trp', 0)")

        print("\n# Peak Values (pre-calculated by API)")
        print("   highest_value = response.get('hmv', 0)  # Highest market value in timeframe")
        print("   lowest_value = response.get('lmv', 0)   # Lowest market value in timeframe")

        print("\n# Historical Data")
        print("   historical_items = response.get('it', [])")
        print("   for item in historical_items:")
        print("       days_since_epoch = item.get('dt', 0)  # Days since 1970-01-01")
        print("       market_value = item.get('mv', 0)      # Market value on that day")
        print("       timestamp = days_since_epoch * 86400  # Convert to Unix timestamp")

    except Exception as e:
        print(f"\n❌ Error fetching data: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    inspect_api_response()
