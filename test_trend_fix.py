"""Test to verify trend calculation fixes"""

from datetime import datetime, timedelta

from rehoboam.value_history import ValueHistoryCache


def create_test_history(price_trajectory: list[int]) -> dict:
    """
    Create test history data with price trajectory

    Args:
        price_trajectory: List of prices from oldest to newest
                         e.g., [5_000_000, 7_000_000, 10_000_000, 7_000_000]
    """
    base_date = int((datetime.now() - timedelta(days=len(price_trajectory))).timestamp())
    day_seconds = 86400

    historical_items = []
    for i, price in enumerate(price_trajectory):
        historical_items.append({"dt": base_date + (i * day_seconds), "mv": price})

    return {
        "it": historical_items,
        "trp": price_trajectory[0],  # Reference price = oldest price
        "hmv": max(price_trajectory),  # Peak
        "lmv": min(price_trajectory),  # Low
    }


def test_scenarios():
    """Test various price movement scenarios"""
    cache = ValueHistoryCache()

    print("Testing Trend Calculation Fix")
    print("=" * 80)

    # Scenario 1: Classic "catching falling knife" - OLD BUG would say "rising"
    print("\n1. FALLING FROM PEAK (The Bug This Fixes)")
    print("-" * 80)
    history = create_test_history(
        [
            5_000_000,  # 21 days ago - reference price
            6_000_000,  # 14 days ago
            10_000_000,  # 7 days ago - PEAK
            7_000_000,  # Yesterday - falling
        ]
    )
    current = 7_000_000

    trend = cache.get_trend_analysis(history, current)
    print("Price trajectory: €5M → €6M → €10M (peak) → €7M (current)")
    print("OLD BEHAVIOR: Would compare €7M to €5M reference = +40% 'rising' ❌")
    print(f"NEW BEHAVIOR: {trend}")
    print(f"Result: {trend['trend']} ({trend['change_pct']:+.1f}%)")
    print("Expected: 'falling' because current is below recent peak")

    # Scenario 2: Genuine rising trend
    print("\n2. GENUINE RISING TREND")
    print("-" * 80)
    history = create_test_history(
        [
            5_000_000,  # 21 days ago
            6_000_000,  # 14 days ago
            7_000_000,  # 7 days ago
            8_000_000,  # Yesterday
        ]
    )
    current = 8_500_000

    trend = cache.get_trend_analysis(history, current)
    print("Price trajectory: €5M → €6M → €7M → €8M → €8.5M (current)")
    print(f"Result: {trend['trend']} ({trend['change_pct']:+.1f}%)")
    print("Expected: 'rising' with positive momentum")

    # Scenario 3: Stable after rise
    print("\n3. STABLE (Small fluctuations)")
    print("-" * 80)
    history = create_test_history(
        [
            7_000_000,  # 21 days ago
            7_100_000,  # 14 days ago
            6_900_000,  # 7 days ago
            7_050_000,  # Yesterday
        ]
    )
    current = 7_000_000

    trend = cache.get_trend_analysis(history, current)
    print("Price trajectory: €7M → €7.1M → €6.9M → €7.05M → €7M (current)")
    print(f"Result: {trend['trend']} ({trend['change_pct']:+.1f}%)")
    print("Expected: 'stable' with minimal movement")

    # Scenario 4: Currently falling vs recent (catching knife)
    print("\n4. CATCHING FALLING KNIFE")
    print("-" * 80)
    history = create_test_history(
        [
            6_000_000,  # 21 days ago
            7_000_000,  # 14 days ago
            8_000_000,  # 7 days ago
            7_500_000,  # Yesterday - started falling
        ]
    )
    current = 6_800_000  # Currently down 9% from yesterday

    trend = cache.get_trend_analysis(history, current)
    print("Price trajectory: €6M → €7M → €8M → €7.5M → €6.8M (current, -9% drop)")
    print(f"Result: {trend['trend']} ({trend['change_pct']:+.1f}%)")
    print("Expected: 'falling' because current dropped >5% from most recent")

    # Scenario 5: Peak detection fallback
    print("\n5. PEAK DETECTION (Fallback)")
    print("-" * 80)
    history = create_test_history(
        [
            5_000_000,  # 21 days ago
            10_000_000,  # 14 days ago - PEAK
        ]
    )
    current = 9_000_000  # 10% below peak

    trend = cache.get_trend_analysis(history, current)
    print("Price trajectory: €5M → €10M (peak) → €9M (current, 10% below peak)")
    print(f"Result: {trend['trend']} ({trend['change_pct']:+.1f}%)")
    print("Expected: 'falling' because >5% below peak")

    print("\n" + "=" * 80)
    print("Test complete! Review results above.")
    print("The fix should correctly identify falling trends and avoid buying peaks.")


if __name__ == "__main__":
    test_scenarios()
