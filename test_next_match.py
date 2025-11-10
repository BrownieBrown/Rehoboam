#!/usr/bin/env python3
"""Test fetching next match date from starting eleven endpoint"""

import json
from datetime import datetime

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings


def test_next_match_date():
    """Test the starting eleven endpoint for next match date"""

    print("=" * 70)
    print("TESTING NEXT MATCH DATE DETECTION")
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

    print("\n3. Fetching starting eleven...")
    try:
        starting_eleven = api.client.get_starting_eleven(league.id)

        print("\n" + "=" * 70)
        print("RAW API RESPONSE")
        print("=" * 70)
        print(json.dumps(starting_eleven, indent=2, ensure_ascii=False))

        print("\n" + "=" * 70)
        print("FIELD ANALYSIS")
        print("=" * 70)

        # Analyze top-level fields
        print("\n📋 Top-level keys:")
        for key in starting_eleven.keys():
            value = starting_eleven[key]
            value_type = type(value).__name__

            if isinstance(value, list):
                print(f"   • '{key}' ({value_type}, length={len(value)})")
            elif isinstance(value, dict):
                print(f"   • '{key}' ({value_type}, keys={list(value.keys())})")
            elif isinstance(value, int | float):
                print(f"   • '{key}' ({value_type}) = {value}")
            elif isinstance(value, str):
                print(f"   • '{key}' ({value_type}) = '{value}'")
            else:
                print(f"   • '{key}' ({value_type}) = {value}")

        print("\n" + "=" * 70)
        print("NEXT MATCH DATE DETECTION")
        print("=" * 70)

        # Try different field names for next match date
        date_fields = ["nm", "nextMatch", "md", "matchDate", "nextMatchDay", "nmd"]

        found_date = False
        for field in date_fields:
            if field in starting_eleven:
                value = starting_eleven[field]
                print(f"\n✓ Found potential match date field: '{field}'")
                print(f"   Raw value: {value}")
                print(f"   Type: {type(value).__name__}")

                # Try to parse as date
                try:
                    if isinstance(value, int | float):
                        # Timestamp (seconds or milliseconds)
                        if value > 10000000000:  # Milliseconds
                            timestamp = value / 1000
                        else:
                            timestamp = value
                        date_obj = datetime.fromtimestamp(timestamp)
                        print(f"   Parsed as timestamp: {date_obj.strftime('%Y-%m-%d %H:%M')}")

                        days_until = (date_obj - datetime.now()).days
                        print(f"   Days until match: {days_until}")
                        found_date = True

                    elif isinstance(value, str):
                        # ISO string
                        date_obj = datetime.fromisoformat(value.replace("Z", "+00:00"))
                        print(f"   Parsed as ISO string: {date_obj.strftime('%Y-%m-%d %H:%M')}")

                        days_until = (date_obj - datetime.now()).days
                        print(f"   Days until match: {days_until}")
                        found_date = True

                except Exception as e:
                    print(f"   ❌ Failed to parse: {e}")

        if not found_date:
            print(f"\n⚠️  No next match date found. Tried fields: {', '.join(date_fields)}")
            print(f"   Available fields: {', '.join(starting_eleven.keys())}")

        print("\n" + "=" * 70)
        print("LINEUP ANALYSIS")
        print("=" * 70)

        # Check lineup field
        lineup = starting_eleven.get("lp", [])
        if lineup and isinstance(lineup, list):
            print(f"\n✓ Found lineup: {len(lineup)} players")
            if len(lineup) > 0:
                first_player = lineup[0]
                print(f"   Player structure: {first_player}")
        else:
            print("\n⚠️  No lineup found in 'lp' field")

    except Exception as e:
        print(f"\n❌ Error fetching starting eleven: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test_next_match_date()
