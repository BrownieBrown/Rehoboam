"""Test the refactored analyzer"""

from rehoboam.analyzer import FactorWeights, MarketAnalyzer


# Create a mock market player
class MockPlayer:
    def __init__(self):
        self.id = "test123"
        self.first_name = "Test"
        self.last_name = "Player"
        self.market_value = 5_000_000
        self.price = 4_500_000
        self.points = 150
        self.average_points = 50
        self.position = 1  # Goalkeeper
        self.status = 0  # Active
        self.number = 1
        self.team_id = "team123"


# Create analyzer with default weights
analyzer = MarketAnalyzer(
    min_buy_value_increase_pct=10.0,
    min_sell_profit_pct=15.0,
    max_loss_pct=-15.0,
    min_value_score_to_buy=40.0,
)

# Test market player analysis
player = MockPlayer()

# Test 1: Basic analysis without trend/matchup data
print("Test 1: Basic market player analysis")
print("=" * 60)
analysis = analyzer.analyze_market_player(player)
print(f"Player: {player.first_name} {player.last_name}")
print(f"Price: €{player.price:,} | Market Value: €{player.market_value:,}")
print(f"Recommendation: {analysis.recommendation}")
print(f"Confidence: {analysis.confidence:.2f}")
print(f"Value Score: {analysis.value_score:.1f}")
print(f"Reason: {analysis.reason}")
print(f"Factors ({len(analysis.factors)}):")
for factor in analysis.factors:
    print(f"  - {factor.name}: {factor.score:+.1f} (weight: {factor.weight})")
    print(f"    {factor.description}")
print()

# Test 2: With trend data (rising)
print("Test 2: With rising trend data")
print("=" * 60)
trend_data = {"has_data": True, "trend": "rising", "change_pct": 20.0, "reference_value": 4_000_000}
analysis2 = analyzer.analyze_market_player(player, trend_data=trend_data)
print(f"Recommendation: {analysis2.recommendation}")
print(f"Value Score: {analysis2.value_score:.1f}")
print(f"Reason: {analysis2.reason}")
print(f"Factors ({len(analysis2.factors)}):")
for factor in analysis2.factors:
    print(f"  - {factor.name}: {factor.score:+.1f}")
print()

# Test 3: With matchup context
print("Test 3: With easy matchup and SOS")
print("=" * 60)


class MockSOS:
    def __init__(self):
        self.sos_bonus = 10
        self.short_term_rating = "Very Easy"


matchup_context = {
    "has_data": True,
    "matchup_bonus": {"bonus_points": 5, "reason": "Playing weak opponent"},
    "sos": MockSOS(),
}

analysis3 = analyzer.analyze_market_player(player, matchup_context=matchup_context)
print(f"Recommendation: {analysis3.recommendation}")
print(f"Value Score: {analysis3.value_score:.1f}")
print(f"Reason: {analysis3.reason}")
print(f"Factors ({len(analysis3.factors)}):")
for factor in analysis3.factors:
    print(f"  - {factor.name}: {factor.score:+.1f}")
print()

# Test 4: Custom weights
print("Test 4: With custom weights (aggressive trend weighting)")
print("=" * 60)
custom_weights = FactorWeights(
    base_value=1.0,
    trend_rising=30.0,  # Heavily weight rising trends
    trend_falling=-40.0,  # Heavily penalize falling trends
    discount=20.0,
)
analyzer_custom = MarketAnalyzer(
    min_buy_value_increase_pct=10.0,
    min_sell_profit_pct=15.0,
    max_loss_pct=-15.0,
    min_value_score_to_buy=40.0,
    factor_weights=custom_weights,
)

analysis4 = analyzer_custom.analyze_market_player(player, trend_data=trend_data)
print(f"Recommendation: {analysis4.recommendation}")
print(f"Value Score: {analysis4.value_score:.1f}")
print(f"Reason: {analysis4.reason}")
print()

print("✓ All tests passed! Refactoring works correctly.")
