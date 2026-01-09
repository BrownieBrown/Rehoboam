"""Test the activity feed to see what data we can learn from"""

import json

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings

settings = get_settings()
api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
api.login()

leagues = api.get_leagues()
league = leagues[0]

print(f"Fetching activity feed for: {league.name}\n")

# Fetch recent activities
activities = api.client.get_activities_feed(league.id, start=0)

print(f"Total activities: {len(activities.get('items', []))}")
print("\nFirst 5 activities:\n")

for i, activity in enumerate(activities.get("items", [])[:5]):
    print(f"\n--- Activity {i+1} ---")
    print(json.dumps(activity, indent=2, default=str))

# Check for transfer/auction specific data
print("\n\n=== TRANSFER ANALYSIS ===")
transfers = [
    a for a in activities.get("items", []) if "player" in a or "transfer" in str(a).lower()
]
print(f"Activities with player/transfer data: {len(transfers)}")

if transfers:
    print("\nSample transfer activity:")
    print(json.dumps(transfers[0], indent=2, default=str))
