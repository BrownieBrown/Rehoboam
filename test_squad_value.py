#!/usr/bin/env python3
"""Check if we can get team value from squad"""

import os

from dotenv import load_dotenv

from rehoboam.kickbase_client import KickbaseV4Client

# Load credentials
load_dotenv()
email = os.environ.get("KICKBASE_EMAIL")
password = os.environ.get("KICKBASE_PASSWORD")

# Login
client = KickbaseV4Client()
print("🔑 Logging in...")
client.login(email, password)

# Get first league
league = client.leagues[0]
print(f"📊 League: {league.name}\n")

# Get squad
squad = client.get_squad(league.id)
print(f"👥 Squad size: {len(squad)} players\n")

# Calculate team value from squad
team_value = sum(player.market_value for player in squad)
print(f"💰 Calculated Team Value: €{team_value:,}")

# Show top 5 most valuable players
print("\n🔝 Top 5 most valuable players:")
sorted_squad = sorted(squad, key=lambda p: p.market_value, reverse=True)[:5]
for i, player in enumerate(sorted_squad, 1):
    print(f"  {i}. {player.first_name} {player.last_name}: €{player.market_value:,}")
