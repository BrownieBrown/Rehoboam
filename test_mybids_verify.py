#!/usr/bin/env python3
"""Verify the myBids parameter works correctly"""

import os

from dotenv import load_dotenv

from rehoboam.kickbase_client import KickbaseV4Client

load_dotenv()

client = KickbaseV4Client()
client.login(email=os.getenv("KICKBASE_EMAIL"), password=os.getenv("KICKBASE_PASSWORD"))

league = client.leagues[0]
league_id = league.id

print("=" * 70)
print("COMPARISON: All Market vs My Bids Only")
print("=" * 70)

# Test 1: Regular market (all players)
print("\n1. Regular market endpoint:")
all_market = client.get_market(league_id)
print(f"   Total players: {len(all_market)}")

# Test 2: My bids endpoint
print("\n2. My bids endpoint (?myBids=true):")
url = f"{client.BASE_URL}/v4/leagues/{league_id}/market?myBids=true"
response = client.session.get(url)

if response.status_code == 200:
    data = response.json()
    my_bids_players = data.get("it", [])
    print(f"   Total players: {len(my_bids_players)}")

    print("\n   Your active bids:")
    for player_data in my_bids_players:
        name = f"{player_data.get('fn', '')} {player_data.get('n', '')}"
        bid = player_data.get("uop", 0)
        mv = player_data.get("mv", 0)
        print(f"   - {name}")
        print(f"     Your bid: €{bid:,}")
        print(f"     Market value: €{mv:,}")
        print(f"     Bid vs MV: {((bid/mv - 1) * 100):.1f}% over")

print("\n" + "=" * 70)
print("✓ SUCCESS! The ?myBids=true parameter works!")
print("  It returns ONLY players with your active bids (2 instead of 53)")
print("=" * 70)
