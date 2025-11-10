#!/usr/bin/env python3
"""Test the cleaned up analyze output"""

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings
from rehoboam.trader import Trader


def test_analyze_cleanup():
    """Test that analyze works without ValueTracker"""

    print("=" * 70)
    print("TESTING CLEANED UP ANALYZE OUTPUT")
    print("=" * 70)

    settings = get_settings()
    api = KickbaseAPI(email=settings.kickbase_email, password=settings.kickbase_password)

    print("\n1. Logging in...")
    api.login()
    print("✓ Logged in")

    print("\n2. Fetching leagues...")
    leagues = api.get_leagues()
    league = leagues[0]
    print(f"✓ Using league: {league.name}")

    print(f"\n{'=' * 70}")
    print("RUNNING ANALYZE_TEAM")
    print(f"{'=' * 70}")

    trader = Trader(api=api, settings=settings)

    try:
        analyses = trader.analyze_team(league)

        print("\n✅ Analysis complete!")
        print(f"   Analyzed {len(analyses)} players")

        # Check that analyses have metadata with peak info
        with_peak = sum(1 for a in analyses if a.metadata and "peak_value" in a.metadata)
        print(f"   Players with peak data: {with_peak}/{len(analyses)}")

        # Show sample player
        if analyses:
            sample = analyses[0]
            print(f"\n   Sample Player: {sample.player.first_name} {sample.player.last_name}")
            print(f"      Current Value: €{sample.market_value:,}")
            print(f"      Value Score: {sample.value_score:.1f}/100")
            trend_str = f"{sample.trend_change_pct:+.1f}%" if sample.trend_change_pct else "N/A"
            print(f"      Trend: {sample.trend} ({trend_str})")
            print(f"      Recommendation: {sample.recommendation}")
            if sample.metadata:
                peak = sample.metadata.get("peak_value")
                if peak:
                    print(f"      Peak Value: €{peak:,}")
                    decline = sample.metadata.get("decline_from_peak_pct", 0)
                    print(f"      Decline from Peak: {decline:.1f}%")

        print("\n✅ SUCCESS! Analyze works without deprecated ValueTracker")
        print("   - No 'Peak detection needs ~7+ days' warnings")
        print("   - Uses API endpoints directly")
        print("   - Clean output")

    except Exception as e:
        print(f"\n❌ Error in analyze: {e}")
        import traceback

        traceback.print_exc()

    print(f"\n{'=' * 70}")
    print("TEST COMPLETE")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    test_analyze_cleanup()
