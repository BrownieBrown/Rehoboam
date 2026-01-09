"""Decode the activity feed structure"""

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings

settings = get_settings()
api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
api.login()

leagues = api.get_leagues()
league = leagues[0]

response = api.client.get_activities_feed(league.id, start=0)

print("=== ACTIVITY TYPES FOUND ===\n")

type_counts = {}
for activity in response.get("af", []):
    t = activity.get("t")
    type_counts[t] = type_counts.get(t, 0) + 1

for t, count in sorted(type_counts.items()):
    print(f"Type {t}: {count} activities")

print("\n=== TRANSFER ACTIVITIES (Type 15) ===\n")

transfers = [a for a in response.get("af", []) if a.get("t") == 15]
print(f"Total transfers in feed: {len(transfers)}")

if transfers:
    print("\nSample transfers with decoded data:\n")
    for i, transfer in enumerate(transfers[:5]):
        data = transfer.get("data", {})

        buyer = data.get("byr", "N/A")
        seller = data.get("slr", "N/A")
        player_name = data.get("pn", "Unknown")
        transfer_price = data.get("trp", 0)
        transfer_type = data.get("t", 0)  # 1=buy, 2=sell

        action = "BOUGHT" if transfer_type == 1 else "SOLD"

        print(f"{i+1}. {buyer if transfer_type == 1 else seller} {action} {player_name}")
        print(f"   Price: €{transfer_price:,}")
        print(f"   Date: {transfer.get('dt')}")
        print()

print("\n=== MARKET VALUE CHANGES (Type 3) ===\n")
mv_changes = [a for a in response.get("af", []) if a.get("t") == 3]
print(f"Total market value updates: {len(mv_changes)}")
