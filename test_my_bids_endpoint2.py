#!/usr/bin/env python3
"""Test additional endpoints that might contain bid info"""

import json
import os

from dotenv import load_dotenv

from rehoboam.kickbase_client import KickbaseV4Client

load_dotenv()


def test_user_and_team_endpoints():
    """Check user/team endpoints for bid information"""

    # Login
    client = KickbaseV4Client()
    client.login(email=os.getenv("KICKBASE_EMAIL"), password=os.getenv("KICKBASE_PASSWORD"))

    league = client.leagues[0]
    league_id = league.id
    user_id = client.user.id

    print(f"Testing with league: {league.name}")
    print(f"User ID: {user_id}\n")
    print("=" * 70)

    # Additional endpoints to try
    endpoints = [
        # User-related
        "/v4/user",
        "/v4/user/profile",
        "/v4/user/me",
        # Team/lineup endpoints
        f"/v4/leagues/{league_id}/me",
        f"/v4/leagues/{league_id}/users/{user_id}",
        f"/v4/leagues/{league_id}/currentuser",
        f"/v4/leagues/{league_id}/team",
        f"/v4/leagues/{league_id}/myteam",
        # Market filters
        f"/v4/leagues/{league_id}/market?userId={user_id}",
        f"/v4/leagues/{league_id}/market?user={user_id}",
        f"/v4/leagues/{league_id}/market?myBids=true",
        f"/v4/leagues/{league_id}/market?filter=myBids",
    ]

    successful = []

    for endpoint in endpoints:
        full_url = f"{client.BASE_URL}{endpoint}"
        print(f"\nTrying: {endpoint}")

        try:
            response = client.session.get(full_url)
            print(f"  Status: {response.status_code}")

            if response.status_code == 200:
                try:
                    data = response.json()
                    print("  ✓ SUCCESS!")

                    # Look for bid-related fields
                    data_str = json.dumps(data, default=str)
                    bid_keywords = ["bid", "offer", "uop", "uoid", "myoffer", "myBid"]

                    found_keywords = [kw for kw in bid_keywords if kw.lower() in data_str.lower()]

                    if found_keywords:
                        print(f"  ⭐ Found bid-related keywords: {found_keywords}")

                    if isinstance(data, dict):
                        print(f"  Top-level keys: {list(data.keys())[:20]}")
                    elif isinstance(data, list):
                        print(f"  List with {len(data)} items")

                    successful.append((endpoint, data))

                except Exception as e:
                    print(f"  ✓ 200 but error: {e}")

            elif response.status_code == 404:
                print("  ✗ Not found")
            else:
                print(f"  ✗ Status {response.status_code}")

        except Exception as e:
            print(f"  ✗ Exception: {e}")

    print("\n" + "=" * 70)
    print(f"SUCCESSFUL ENDPOINTS: {len(successful)}\n")

    if successful:
        for endpoint, data in successful:
            print(f"\n✓ {endpoint}")
            print(f"  Keys/preview: {str(data)[:300]}")

    # Also check if team_info contains bid info
    print("\n" + "=" * 70)
    print("CHECKING get_team_info() for bid data:")
    print("-" * 70)

    try:
        team_info = client.get_team_info(league_id)
        print(f"Team info keys: {list(team_info.keys())}")

        # Look for bid-related fields
        data_str = json.dumps(team_info, default=str)
        if "bid" in data_str.lower() or "offer" in data_str.lower():
            print("⭐ Found bid/offer data in team_info!")
            print(json.dumps(team_info, indent=2, default=str)[:500])

    except Exception as e:
        print(f"Error getting team_info: {e}")


if __name__ == "__main__":
    try:
        test_user_and_team_endpoints()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
