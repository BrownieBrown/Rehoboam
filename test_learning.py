#!/usr/bin/env python3
"""Test the value-bounded learning system"""

from rehoboam.bid_learner import BidLearner
from rehoboam.bidding_strategy import SmartBidding


def test_value_bounded_learning():
    """Test that learning respects value ceilings"""

    print("=" * 70)
    print("VALUE-BOUNDED LEARNING TEST")
    print("=" * 70)

    # Initialize learner and bidding strategy
    learner = BidLearner()
    bidding = SmartBidding(bid_learner=learner)

    print("\n📚 Current Learning Database:")
    stats = learner.get_statistics()
    print(f"  Total auctions: {stats['total_auctions']}")
    print(f"  Wins: {stats['wins']}")
    print(f"  Losses: {stats['losses']}")
    if stats.get("avg_losing_overbid"):
        print(f"  Our avg losing overbid: {stats['avg_losing_overbid']}%")

    print("\n" + "=" * 70)
    print("TEST CASE 1: High Value Player (Should bid aggressively)")
    print("=" * 70)

    asking_price_1 = 10_000_000
    market_value_1 = 11_000_000
    value_score_1 = 85.0
    predicted_value_1 = 13_000_000  # We think he'll be worth €13M

    print("\n📊 Player Info:")
    print(f"  Asking Price: €{asking_price_1:,}")
    print(f"  Current Market Value: €{market_value_1:,}")
    print(f"  Value Score: {value_score_1}/100")
    print(f"  Predicted Future Value: €{predicted_value_1:,}")

    # Get learned recommendation
    learned = learner.get_recommended_overbid(
        asking_price=asking_price_1,
        value_score=value_score_1,
        market_value=market_value_1,
        predicted_future_value=predicted_value_1,
    )

    print("\n💡 Learned Recommendation:")
    print(f"  Recommended overbid: {learned['recommended_overbid_pct']}%")
    print(f"  Max bid: €{learned['max_bid']:,}")
    print(f"  Reason: {learned['reason']}")
    print(f"  Value ceiling applied: {learned.get('value_ceiling_applied', False)}")

    # Calculate actual bid
    bid_1 = bidding.calculate_bid(
        asking_price=asking_price_1,
        market_value=market_value_1,
        value_score=value_score_1,
        confidence=0.85,
        predicted_future_value=predicted_value_1,
    )

    print("\n💰 Final Bid Decision:")
    print(f"  Recommended bid: €{bid_1.recommended_bid:,}")
    print(f"  Overbid: €{bid_1.overbid_amount:,} ({bid_1.overbid_pct:.1f}%)")
    print(f"  Max profitable: €{bid_1.max_profitable_bid:,}")
    print(f"  Reasoning: {bid_1.reasoning}")

    print("\n" + "=" * 70)
    print("TEST CASE 2: Player Above Value Ceiling (Should SKIP)")
    print("=" * 70)

    asking_price_2 = 15_000_000
    market_value_2 = 14_500_000
    value_score_2 = 55.0  # Decent but not great
    predicted_value_2 = 15_500_000  # Only worth €15.5M to us

    print("\n📊 Player Info:")
    print(f"  Asking Price: €{asking_price_2:,}")
    print(f"  Current Market Value: €{market_value_2:,}")
    print(f"  Value Score: {value_score_2}/100")
    print(f"  Predicted Future Value: €{predicted_value_2:,}")
    print("  ⚠️ Value ceiling is only €500k above asking!")

    # Get learned recommendation
    learned_2 = learner.get_recommended_overbid(
        asking_price=asking_price_2,
        value_score=value_score_2,
        market_value=market_value_2,
        predicted_future_value=predicted_value_2,
    )

    print("\n💡 Learned Recommendation:")
    print(f"  Recommended overbid: {learned_2['recommended_overbid_pct']}%")
    print(f"  Max bid: €{learned_2['max_bid']:,}")
    print(f"  Reason: {learned_2['reason']}")
    print(f"  Value ceiling applied: {learned_2.get('value_ceiling_applied', False)}")

    # Calculate actual bid
    bid_2 = bidding.calculate_bid(
        asking_price=asking_price_2,
        market_value=market_value_2,
        value_score=value_score_2,
        confidence=0.65,
        predicted_future_value=predicted_value_2,
    )

    print("\n💰 Final Bid Decision:")
    print(f"  Recommended bid: €{bid_2.recommended_bid:,}")
    print(f"  Overbid: €{bid_2.overbid_amount:,} ({bid_2.overbid_pct:.1f}%)")
    print(f"  Max profitable: €{bid_2.max_profitable_bid:,}")
    print(f"  Reasoning: {bid_2.reasoning}")

    if bid_2.overbid_pct < 3.5:
        print("\n  ⚠️ RECOMMENDATION: SKIP - Too close to value ceiling!")
        print("  Human bidders will likely overbid more than we can afford")

    print("\n" + "=" * 70)
    print("TEST CASE 3: Yan-like Scenario (High asking, would need 36% overbid)")
    print("=" * 70)

    asking_price_3 = 12_000_000
    market_value_3 = 13_000_000
    value_score_3 = 70.0
    predicted_value_3 = 14_500_000  # We think worth €14.5M

    print("\n📊 Player Info:")
    print(f"  Asking Price: €{asking_price_3:,}")
    print(f"  Current Market Value: €{market_value_3:,}")
    print(f"  Value Score: {value_score_3}/100")
    print(f"  Predicted Future Value: €{predicted_value_3:,}")
    print(f"  Competitor might bid 36% over (€{int(asking_price_3 * 1.36):,})")

    # Get learned recommendation
    learned_3 = learner.get_recommended_overbid(
        asking_price=asking_price_3,
        value_score=value_score_3,
        market_value=market_value_3,
        predicted_future_value=predicted_value_3,
    )

    print("\n💡 Learned Recommendation:")
    print(f"  Recommended overbid: {learned_3['recommended_overbid_pct']}%")
    print(f"  Max bid: €{learned_3['max_bid']:,}")
    print(f"  Reason: {learned_3['reason']}")
    print(f"  Value ceiling applied: {learned_3.get('value_ceiling_applied', False)}")

    # Calculate actual bid
    bid_3 = bidding.calculate_bid(
        asking_price=asking_price_3,
        market_value=market_value_3,
        value_score=value_score_3,
        confidence=0.80,
        predicted_future_value=predicted_value_3,
    )

    print("\n💰 Final Bid Decision:")
    print(f"  Our max bid: €{bid_3.recommended_bid:,} ({bid_3.overbid_pct:.1f}%)")
    print(f"  Competitor likely bids: €{int(asking_price_3 * 1.36):,} (36%)")
    print(f"  Difference: €{int(asking_price_3 * 1.36) - bid_3.recommended_bid:,}")
    print(f"  Reasoning: {bid_3.reasoning}")

    if bid_3.recommended_bid < asking_price_3 * 1.36:
        print("\n  ⚠️ RECOMMENDATION: SKIP or accept we might lose")
        print("  We won't match irrational overbids beyond our value ceiling")
        print("  This protects us from overpaying like the Yan scenario")

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("\n✅ Value-Bounded Learning:")
    print("  • Learns from past auctions (Yan: 36% overbid)")
    print("  • BUT: Never exceeds predicted future value")
    print("  • Protects against irrational human overbidding")
    print("  • Accepts losing some auctions to maintain profitability")
    print("\n✅ When We Lose:")
    print("  • Track if winner actually made a good deal")
    print("  • If player value rises → adjust our predictions")
    print("  • If player value falls → we were right to skip")


if __name__ == "__main__":
    test_value_bounded_learning()
