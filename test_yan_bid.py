#!/usr/bin/env python
"""Test script to check Yan Diomande auction status"""

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings
from rehoboam.trader import Trader


def main():
    settings = get_settings()
    api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)

    print("Logging in...")
    api.login()

    leagues = api.get_leagues()
    league = leagues[0]

    print(f"\nChecking league: {league.name}")
    print(f"Your user ID: {api.user.id}")

    # Get market data
    print("\nFetching market data...")
    market_players = api.get_market(league)

    # Find Yan Diomande
    yan = None
    for player in market_players:
        if "Yan" in player.first_name and "Diomande" in player.last_name:
            yan = player
            break

    if yan:
        print("\n✓ Found Yan Diomande on market!")
        print(f"  Player ID: {yan.id}")
        print(f"  Price: €{yan.price:,}")
        print(f"  Market Value: €{yan.market_value:,}")
        print(f"  Offer Count: {yan.offer_count}")
        print(f"  Listed At: {yan.listed_at}")

        if yan.user_offer_price:
            print(f"\n  YOUR BID: €{yan.user_offer_price:,}")
            print(f"  Your Offer ID: {yan.user_offer_id}")

            # Check if it matches your user ID
            if yan.has_user_offer(api.user.id):
                print("  ✓ Confirmed: This is YOUR bid!")
            else:
                print("  ⚠️  Bid user ID doesn't match your ID")
        else:
            print("\n  No active bid from you")

        if yan.offers:
            print(f"\n  All offers ({len(yan.offers)}):")
            for i, offer in enumerate(yan.offers, 1):
                print(f"    {i}. {offer.get('unm', 'Unknown')}: €{offer.get('uop', 0):,}")
    else:
        print("\n✗ Yan Diomande NOT found on market")
        print("   Checking if in your squad...")

        squad = api.get_squad(league)
        yan_in_squad = None
        for player in squad:
            if "Yan" in player.first_name and "Diomande" in player.last_name:
                yan_in_squad = player
                break

        if yan_in_squad:
            print("   ✓ Yan Diomande IS in your squad!")
            print("   → YOU WON THE AUCTION!")
        else:
            print("   ✗ Yan Diomande NOT in your squad")
            print("   → Auction ended, you did not win")

    # Test bid monitor
    print("\n" + "=" * 60)
    print("Testing Bid Monitor...")
    print("=" * 60)

    trader = Trader(api, settings)

    # Check if we have Yan registered
    if "10771" in trader.bid_monitor.pending_bids:
        bid_status = trader.bid_monitor.pending_bids["10771"]
        print("\n✓ Yan bid registered in monitor:")
        print(f"  Player: {bid_status.player_name}")
        print(f"  Amount: €{bid_status.bid_amount:,}")
        print(f"  Status: {bid_status.status}")
        print(f"  Placed: {bid_status.placed_at}")

        # Check current status
        print("\nChecking auction status...")
        status = trader.bid_monitor.check_bid_status(league, "10771")
        print(f"  Current status: {status}")

    else:
        print("\n✗ No bid registered for Yan Diomande in monitor")
        print("   (Expected - bid was placed before monitor system was added)")
        print("\nWould you like to manually register the bid? (for testing)")

        if yan and yan.user_offer_price:
            print("\n   To register manually, the bot would track:")
            print("   - Player: Yan Diomande")
            print(f"   - Bid: €{yan.user_offer_price:,}")
            print("   - Auction: Currently active")


if __name__ == "__main__":
    main()
