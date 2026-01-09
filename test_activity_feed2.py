"""Test the activity feed - raw response"""

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
response = api.client.get_activities_feed(league.id, start=0)

print("Raw response:")
print(json.dumps(response, indent=2, default=str))
