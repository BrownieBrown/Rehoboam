#!/usr/bin/env python3
"""Test script to find the 'My Bids' endpoint"""

import os

from dotenv import load_dotenv

from rehoboam.kickbase_client import KickbaseV4Client

load_dotenv()


def test_my_bids_endpoints():
    """Try different endpoint variations to find 'My Bids'"""

    # Login
    client = KickbaseV4Client()
    client.login(email=os.getenv("KICKBASE_EMAIL"), password=os.getenv("KICKBASE_PASSWORD"))

    # Get first league
    if not client.leagues:
        print("No leagues found!")
        return

    league = client.leagues[0]
    league_id = league.id
    print(f"Testing with league: {league.name} (ID: {league_id})")
    print(f"User ID: {client.user.id}\n")
    print("=" * 70)

    # List of possible endpoints to try
    endpoints = [
        # Most likely
        f"/v4/leagues/{league_id}/market/offers",
        f"/v4/leagues/{league_id}/mybids",
        f"/v4/leagues/{league_id}/bids",
        f"/v4/leagues/{league_id}/my-bids",
        f"/v4/leagues/{league_id}/market/mybids",
        f"/v4/leagues/{league_id}/market/my-bids",
        f"/v4/leagues/{league_id}/user/offers",
        f"/v4/leagues/{league_id}/user/bids",
        # Less likely but worth trying
        f"/v4/leagues/{league_id}/offers",
        f"/v4/leagues/{league_id}/market/user/offers",
        "/v4/user/offers",
        "/v4/user/bids",
        "/v4/user/market/offers",
        # With leagueId query param
        f"/v4/market/offers?leagueId={league_id}",
        f"/v4/mybids?leagueId={league_id}",
        f"/v4/bids?leagueId={league_id}",
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
                    print("  ✓ SUCCESS! Got data:")
                    print(f"  Type: {type(data)}")

                    if isinstance(data, dict):
                        print(f"  Keys: {list(data.keys())}")
                        # If it's a dict with a list of bids
                        for key in ["bids", "offers", "items", "data", "o", "b"]:
                            if key in data and isinstance(data[key], list):
                                print(f"  Found list at key '{key}' with {len(data[key])} items")
                                if data[key]:
                                    print(f"  First item keys: {list(data[key][0].keys())}")
                    elif isinstance(data, list):
                        print(f"  List length: {len(data)}")
                        if data:
                            print(f"  First item: {data[0]}")

                    successful.append((endpoint, data))

                except Exception as e:
                    print(f"  ✓ 200 but error parsing JSON: {e}")

            elif response.status_code == 404:
                print("  ✗ Not found")
            elif response.status_code == 401:
                print("  ✗ Unauthorized (auth issue)")
            elif response.status_code == 403:
                print("  ✗ Forbidden")
            else:
                print(f"  ✗ Error: {response.text[:100]}")

        except Exception as e:
            print(f"  ✗ Exception: {e}")

    print("\n" + "=" * 70)
    print(f"\nSUCCESSFUL ENDPOINTS: {len(successful)}")

    if successful:
        for endpoint, data in successful:
            print(f"\n✓ {endpoint}")
            print(f"  Data preview: {str(data)[:200]}")
    else:
        print("\nNo successful endpoints found.")
        print("\nFallback: Use get_market() and filter for has_user_offer()")
        print("This is what the bot currently does, but it's inefficient.")

    # Show current method
    print("\n" + "=" * 70)
    print("CURRENT METHOD (inefficient):")
    print("-" * 70)

    market_players = client.get_market(league_id)
    user_bids = [p for p in market_players if p.has_user_offer(client.user.id)]

    print(f"Total market players: {len(market_players)}")
    print(f"Players with your bids: {len(user_bids)}")

    if user_bids:
        print("\nYour active bids:")
        for player in user_bids:
            print(f"  - {player.first_name} {player.last_name}")
            print(f"    Your bid: €{player.user_offer_price:,}")
            print(f"    Market value: €{player.market_value:,}")
    else:
        print("\nNo active bids found (or endpoint works differently)")


if __name__ == "__main__":
    try:
        test_my_bids_endpoints()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
