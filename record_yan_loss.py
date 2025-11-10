#!/usr/bin/env python3
"""Manually record the Yan Diomande auction loss for learning"""

from datetime import datetime

from rehoboam.bid_learner import AuctionOutcome, BidLearner


def main():
    """Record Yan Diomande auction loss"""

    # Initialize learner
    learner = BidLearner()

    # Yan Diomande auction details
    asking_price = 12477338
    our_bid = 14097338
    winning_bid = 17000000

    # Calculate overbid percentages
    our_overbid_pct = ((our_bid - asking_price) / asking_price) * 100
    winning_overbid_pct = ((winning_bid - asking_price) / asking_price) * 100

    # Create outcome record
    outcome = AuctionOutcome(
        player_id="10771",
        player_name="Yan Diomande",
        our_bid=our_bid,
        asking_price=asking_price,
        our_overbid_pct=our_overbid_pct,
        won=False,
        winning_bid=winning_bid,
        winning_overbid_pct=winning_overbid_pct,
        winner_user_id="1821396",
        timestamp=datetime.now().timestamp(),
        player_value_score=None,  # Unknown at time of bid
        market_value=None,  # Unknown at time of bid
    )

    # Record it
    learner.record_outcome(outcome)

    print("✓ Recorded Yan Diomande auction loss")
    print(f"  Our bid: €{our_bid:,} (+{our_overbid_pct:.1f}%)")
    print(f"  Winning bid: €{winning_bid:,} (+{winning_overbid_pct:.1f}%)")
    print(f"  Winner: User {outcome.winner_user_id}")
    print(
        f"\n  We bid {our_overbid_pct:.1f}% over asking, but winner bid {winning_overbid_pct:.1f}% over"
    )
    print(f"  We saved €{winning_bid - our_bid:,} by not matching")

    # Show learning statistics
    stats = learner.get_statistics()
    print("\n📊 Learning Database Statistics:")
    print(f"  Total auctions tracked: {stats['total_auctions']}")
    print(f"  Wins: {stats['wins']}")
    print(f"  Losses: {stats['losses']}")
    if stats["total_auctions"] > 0:
        print(f"  Win rate: {stats['win_rate']}%")

    # Analyze the competitor who beat us
    competitor = learner.analyze_competitor("1821396")
    print("\n🔍 Competitor Analysis (User 1821396):")
    print(f"  Times beaten us: {competitor['times_beaten_us']}")
    if competitor.get("avg_overbid"):
        print(f"  Average overbid: {competitor['avg_overbid']}%")
        print(f"  Message: {competitor['message']}")


if __name__ == "__main__":
    main()
