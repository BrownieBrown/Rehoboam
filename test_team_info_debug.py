#!/usr/bin/env python3
"""Debug script to see what the /me endpoint actually returns"""

import json
import os

from dotenv import load_dotenv

from rehoboam.kickbase_client import KickbaseV4Client

# Load credentials
load_dotenv()
email = os.environ.get("KICKBASE_EMAIL")
password = os.environ.get("KICKBASE_PASSWORD")

if not email or not password:
    print("❌ Missing KICKBASE_EMAIL or KICKBASE_PASSWORD environment variables")
    exit(1)

# Login
client = KickbaseV4Client()
print("🔑 Logging in...")
client.login(email, password)

# Get first league
league = client.leagues[0]
print(f"📊 League: {league.name}\n")

# Make raw request to /me endpoint to see full response
url = f"{client.BASE_URL}/v4/leagues/{league.id}/me"
response = client.session.get(url)

if response.status_code == 200:
    data = response.json()
    print("=" * 80)
    print("FULL /me ENDPOINT RESPONSE:")
    print("=" * 80)
    print(json.dumps(data, indent=2))
    print("=" * 80)

    # Check what fields exist
    print("\n📋 Available fields in response:")
    for key in sorted(data.keys()):
        value = data[key]
        if isinstance(value, int | float | str | bool):
            print(f"  {key}: {value} (type: {type(value).__name__})")
        else:
            print(f"  {key}: {type(value).__name__}")

    # Specifically look for team value fields
    print("\n🔍 Looking for team value fields:")
    team_value_keys = [
        k
        for k in data.keys()
        if "team" in k.lower() or "value" in k.lower() or k in ["tv", "tmv", "val"]
    ]
    if team_value_keys:
        print(f"  Found potential team value keys: {team_value_keys}")
        for key in team_value_keys:
            print(f"    {key} = {data[key]}")
    else:
        print("  ⚠️  No obvious team value fields found!")

    # Try get_team_info() to see what it returns
    print("\n📊 get_team_info() returns:")
    team_info = client.get_team_info(league.id)
    print(json.dumps(team_info, indent=2))

else:
    print(f"❌ Failed to fetch /me endpoint: {response.status_code}")
    print(response.text)
