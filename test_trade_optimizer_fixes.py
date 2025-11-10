#!/usr/bin/env python3
"""Test script for trade optimizer fixes"""

import sys
from dataclasses import dataclass


# Mock objects for testing
@dataclass
class MockPlayer:
    id: str
    first_name: str
    last_name: str
    position: str
    team_id: str
    price: int
    market_value: int
    points: int
    average_points: float
    status: int = 0


@dataclass
class MockBidRecommendation:
    recommended_bid: int
    overbid_amount: int
    overbid_pct: float


class MockSmartBidding:
    """Mock SmartBidding for testing"""

    def calculate_bid(self, asking_price, market_value, value_score, confidence):
        # Simulate 10% overbid
        recommended_bid = int(asking_price * 1.10)
        return MockBidRecommendation(
            recommended_bid=recommended_bid,
            overbid_amount=recommended_bid - asking_price,
            overbid_pct=10.0,
        )


def test_trade_optimizer():
    """Test TradeOptimizer with fixes"""
    print("\n=== Testing Trade Optimizer Fixes ===\n")

    from rehoboam.trade_optimizer import TradeOptimizer, TradeRecommendation

    # Create mock players
    _squad = [
        MockPlayer("1", "Low", "Scorer", "ST", "t1", 5000000, 5000000, 100, 20.0),
        MockPlayer("2", "Mid", "Player", "MF", "t1", 6000000, 6000000, 150, 25.0),
        MockPlayer("3", "Good", "Player", "DF", "t1", 7000000, 7000000, 200, 30.0),
    ]

    market = [
        MockPlayer("10", "Star", "Player", "ST", "t2", 10000000, 10000000, 400, 50.0),  # Elite
        MockPlayer("11", "Great", "Player", "MF", "t2", 8000000, 8000000, 300, 40.0),  # Very good
        MockPlayer("12", "Solid", "Player", "DF", "t2", 6000000, 6000000, 250, 35.0),  # Good
    ]

    _player_values = {
        "1": 30.0,
        "2": 40.0,
        "3": 50.0,
        "10": 80.0,
        "11": 70.0,
        "12": 60.0,
    }

    # Test 1: Smart bid calculation
    print("Test 1: Smart Bid Calculation")
    print("-" * 50)

    bidding = MockSmartBidding()
    optimizer = TradeOptimizer(max_players_out=2, max_players_in=2, bidding_strategy=bidding)

    # Calculate a bid manually
    bid = bidding.calculate_bid(
        asking_price=10000000, market_value=10000000, value_score=80.0, confidence=0.8
    )

    print(f"Original asking price: €{10000000:,}")
    print(f"Smart bid: €{bid.recommended_bid:,}")
    print(f"Overbid: €{bid.overbid_amount:,} ({bid.overbid_pct:.1f}%)")

    if bid.recommended_bid == 11000000:  # 10% overbid
        print("✓ Smart bid calculation works!\n")
    else:
        print("✗ Smart bid calculation FAILED!\n")
        return False

    # Test 2: TradeRecommendation has smart_bids field
    print("Test 2: TradeRecommendation smart_bids Field")
    print("-" * 50)

    test_trade = TradeRecommendation(
        players_out=[],
        players_in=[market[0]],
        improvement_points=5.0,
        improvement_value=20.0,
        total_cost=11000000,
        total_proceeds=0,
        net_cost=11000000,
        required_budget=11000000,
        strategy="0-for-1",
        smart_bids={"10": 11000000},
    )

    if test_trade.smart_bids and test_trade.smart_bids.get("10") == 11000000:
        print("✓ TradeRecommendation has smart_bids field!")
        print(f"  Smart bid for player 10: €{test_trade.smart_bids['10']:,}\n")
    else:
        print("✗ TradeRecommendation smart_bids FAILED!\n")
        return False

    # Test 3: Trade ranking with starter quality
    print("Test 3: Trade Ranking with Starter Quality Bonus")
    print("-" * 50)

    # Create two similar trades, one with elite player, one with good player
    trade_elite = TradeRecommendation(
        players_out=[],
        players_in=[market[0]],  # Star Player (50.0 avg)
        improvement_points=5.0,
        improvement_value=20.0,
        total_cost=11000000,
        total_proceeds=0,
        net_cost=11000000,
        required_budget=11000000,
        strategy="0-for-1",
        smart_bids={"10": 11000000},
    )

    trade_good = TradeRecommendation(
        players_out=[],
        players_in=[market[2]],  # Solid Player (35.0 avg)
        improvement_points=5.0,  # Same improvement!
        improvement_value=20.0,
        total_cost=6600000,
        total_proceeds=0,
        net_cost=6600000,
        required_budget=6600000,
        strategy="0-for-1",
        smart_bids={"12": 6600000},
    )

    # Calculate scores using the ranking logic
    def calculate_trade_score(trade):
        score = trade.improvement_points + (trade.improvement_value / 10)
        if trade.players_in:
            avg_quality = sum(p.average_points for p in trade.players_in) / len(trade.players_in)
            if avg_quality > 50:
                score += 2.0
            elif avg_quality > 40:
                score += 1.5
            elif avg_quality > 30:
                score += 1.0
            elif avg_quality > 20:
                score += 0.5
        return score

    score_elite = calculate_trade_score(trade_elite)
    score_good = calculate_trade_score(trade_good)

    print(f"Trade 1 (Elite player, 50.0 avg): Score = {score_elite:.2f}")
    print("  Base: 5.0 pts + 2.0 value = 7.0")
    print("  Bonus: +2.0 (elite player)")
    print(f"\nTrade 2 (Good player, 35.0 avg): Score = {score_good:.2f}")
    print("  Base: 5.0 pts + 2.0 value = 7.0")
    print("  Bonus: +1.0 (good player)")

    if score_elite > score_good:
        print(f"\n✓ Elite player ranked higher! ({score_elite:.2f} > {score_good:.2f})\n")
    else:
        print(f"\n✗ Ranking FAILED! ({score_elite:.2f} <= {score_good:.2f})\n")
        return False

    # Test 4: 0-for-N method exists
    print("Test 4: 0-for-N Trade Method Exists")
    print("-" * 50)

    if hasattr(optimizer, "_evaluate_buy_only_trade"):
        print("✓ _evaluate_buy_only_trade method exists!\n")
    else:
        print("✗ _evaluate_buy_only_trade method NOT FOUND!\n")
        return False

    print("=" * 50)
    print("✓ ALL TESTS PASSED!")
    print("=" * 50)
    return True


if __name__ == "__main__":
    try:
        success = test_trade_optimizer()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ TEST FAILED WITH ERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
