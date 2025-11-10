#!/usr/bin/env python3
"""Test competition-based endpoints for player statistics and market value"""

import json
from datetime import datetime

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings


def test_competition_endpoints():
    """Test /competitions/1/players endpoints"""

    print("=" * 70)
    print("TESTING COMPETITION-BASED ENDPOINTS")
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
    print(f"   League ID: {league.id}")

    print("\n3. Finding test player (Rocco Reitz)...")
    market = api.get_market(league)

    test_player = None
    for player in market:
        if "reitz" in f"{player.first_name} {player.last_name}".lower():
            test_player = player
            break

    if not test_player:
        print("   Rocco Reitz not on market, using first player...")
        test_player = market[0] if market else None

    if not test_player:
        print("❌ No players found!")
        return

    print(f"✓ Testing with: {test_player.first_name} {test_player.last_name}")
    print(f"   Player ID: {test_player.id}")

    # Test 1: Player Statistics
    print(f"\n{'=' * 70}")
    print("TEST 1: PLAYER STATISTICS")
    print(f"{'=' * 70}")

    url_stats = (
        f"https://api.kickbase.com/v4/competitions/1/players/{test_player.id}?leagueId={league.id}"
    )
    print(f"\nURL: {url_stats}")

    try:
        response = api.client.session.get(url_stats)

        if response.status_code == 200:
            data = response.json()
            print(f"\n✅ Success! Status: {response.status_code}")
            print("\n📋 Raw Response:")
            print(json.dumps(data, indent=2, ensure_ascii=False))

            print(f"\n{'=' * 70}")
            print("USEFUL FIELDS FROM STATISTICS")
            print(f"{'=' * 70}")

            # Market value
            if "marketValue" in data:
                print(f"\n💰 Current Market Value: €{data['marketValue']:,}")

            # Points
            if "points" in data:
                print(f"🎯 Total Points: {data['points']}")
            if "averagePoints" in data:
                print(f"📊 Average Points: {data['averagePoints']:.1f}")

            # Status
            if "status" in data:
                print(f"📍 Status: {data['status']}")

            # Team
            if "teamId" in data:
                print(f"🏟️  Team ID: {data['teamId']}")

        else:
            print(f"\n❌ Failed! Status: {response.status_code}")
            print(f"Response: {response.text}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()

    # Test 2: Market Value History (3 months)
    print(f"\n{'=' * 70}")
    print("TEST 2: MARKET VALUE HISTORY (3 MONTHS / 92 DAYS)")
    print(f"{'=' * 70}")

    url_mv_3m = (
        f"https://api.kickbase.com/v4/competitions/1/players/{test_player.id}/marketValue/92"
    )
    print(f"\nURL: {url_mv_3m}")

    try:
        response = api.client.session.get(url_mv_3m)

        if response.status_code == 200:
            data = response.json()
            print(f"\n✅ Success! Status: {response.status_code}")
            print("\n📋 Raw Response:")
            print(json.dumps(data, indent=2, ensure_ascii=False))

            print(f"\n{'=' * 70}")
            print("MARKET VALUE DATA ANALYSIS")
            print(f"{'=' * 70}")

            # The "it" array
            if "it" in data:
                it_array = data["it"]
                print(f"\n📈 Historical Data ('it' array): {len(it_array)} data points")

                if len(it_array) > 0:
                    print("\n   First 5 data points:")
                    for i, item in enumerate(it_array[:5]):
                        dt = item.get("dt", 0)
                        mv = item.get("mv", 0)

                        # Convert days since epoch to date
                        date = datetime.fromtimestamp(dt * 86400)
                        print(f"      {i+1}. Date: {date.strftime('%Y-%m-%d')}, Value: €{mv:,}")

                    if len(it_array) > 5:
                        print(f"      ... ({len(it_array) - 5} more)")
                        last = it_array[-1]
                        dt = last.get("dt", 0)
                        mv = last.get("mv", 0)
                        date = datetime.fromtimestamp(dt * 86400)
                        print(
                            f"      {len(it_array)}. Date: {date.strftime('%Y-%m-%d')}, Value: €{mv:,}"
                        )

                    # Calculate trend
                    print("\n   📊 Trend Analysis:")
                    first_value = it_array[0].get("mv", 0)
                    last_value = it_array[-1].get("mv", 0)

                    if first_value > 0:
                        change = last_value - first_value
                        change_pct = (change / first_value) * 100

                        print(f"      First Value (92 days ago): €{first_value:,}")
                        print(f"      Last Value (today): €{last_value:,}")
                        print(f"      Total Change: €{change:,} ({change_pct:+.1f}%)")

                        if change_pct > 5:
                            print("      Direction: 🟢 RISING")
                        elif change_pct < -5:
                            print("      Direction: 🔴 FALLING")
                        else:
                            print("      Direction: ⚪ STABLE")

                    # Find peak
                    values = [item.get("mv", 0) for item in it_array]
                    peak_value = max(values)
                    peak_idx = values.index(peak_value)
                    peak_item = it_array[peak_idx]
                    peak_date = datetime.fromtimestamp(peak_item.get("dt", 0) * 86400)

                    print("\n   🏔️  Peak Analysis:")
                    print(f"      Peak Value: €{peak_value:,}")
                    print(f"      Peak Date: {peak_date.strftime('%Y-%m-%d')}")

                    if last_value < peak_value:
                        decline = ((last_value - peak_value) / peak_value) * 100
                        days_since_peak = len(it_array) - peak_idx - 1
                        print(f"      Current vs Peak: {decline:.1f}%")
                        print(f"      Days Since Peak: {days_since_peak}")
                        print("      Status: ⚠️  BELOW PEAK")
                    else:
                        print("      Status: ✅ AT PEAK")

                    # Recent trend (last 14 days)
                    if len(it_array) >= 14:
                        recent = it_array[-14:]
                        recent_first = recent[0].get("mv", 0)
                        recent_last = recent[-1].get("mv", 0)

                        if recent_first > 0:
                            recent_change_pct = ((recent_last - recent_first) / recent_first) * 100
                            print("\n   📅 Recent Trend (14 days):")
                            print(
                                f"      Change: €{recent_last - recent_first:,} ({recent_change_pct:+.1f}%)"
                            )

                            if recent_change_pct > 3:
                                print("      Direction: 🟢 RISING")
                            elif recent_change_pct < -3:
                                print("      Direction: 🔴 FALLING")
                            else:
                                print("      Direction: ⚪ STABLE")

            # Other fields
            if "trp" in data:
                print(f"\n💰 Transfer Price (trp): €{data['trp']:,}")
            if "hmv" in data:
                print(f"🏔️  Highest Value (hmv): €{data['hmv']:,}")
            if "lmv" in data:
                print(f"📉 Lowest Value (lmv): €{data['lmv']:,}")

        else:
            print(f"\n❌ Failed! Status: {response.status_code}")
            print(f"Response: {response.text}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()

    # Test 3: Market Value History (1 year)
    print(f"\n{'=' * 70}")
    print("TEST 3: MARKET VALUE HISTORY (1 YEAR / 365 DAYS)")
    print(f"{'=' * 70}")

    url_mv_1y = (
        f"https://api.kickbase.com/v4/competitions/1/players/{test_player.id}/marketValue/365"
    )
    print(f"\nURL: {url_mv_1y}")

    try:
        response = api.client.session.get(url_mv_1y)

        if response.status_code == 200:
            data = response.json()
            print(f"\n✅ Success! Status: {response.status_code}")

            if "it" in data:
                it_array = data["it"]
                print(f"\n📈 Historical Data: {len(it_array)} data points over 1 year")

                # Show summary
                if len(it_array) > 0:
                    first_value = it_array[0].get("mv", 0)
                    last_value = it_array[-1].get("mv", 0)
                    values = [item.get("mv", 0) for item in it_array]

                    print(f"   First Value: €{first_value:,}")
                    print(f"   Last Value: €{last_value:,}")
                    print(f"   Peak Value: €{max(values):,}")
                    print(f"   Lowest Value: €{min(values):,}")

                    if first_value > 0:
                        change_pct = ((last_value - first_value) / first_value) * 100
                        print(f"   1-Year Change: {change_pct:+.1f}%")
        else:
            print(f"\n❌ Failed! Status: {response.status_code}")

    except Exception as e:
        print(f"\n❌ Error: {e}")

    print(f"\n{'=' * 70}")
    print("PROFIT OPPORTUNITY DETECTION")
    print(f"{'=' * 70}")

    print("\nIf this player is Rocco Reitz:")
    print("   User said: €12M (17.10) → €14M (now), peak €19M")
    print("   Bot should detect:")
    print("      ✅ Rising trend (check 14-day trend)")
    print("      ✅ Below peak (current vs €19M peak)")
    print("      ✅ Undervalued (has room to grow to peak)")
    print("      ✅ PROFIT OPPORTUNITY!")


if __name__ == "__main__":
    test_competition_endpoints()
