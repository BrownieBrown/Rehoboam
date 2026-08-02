"""Integration: the bot's live scoring path uses v2 and real point units."""

from __future__ import annotations

from rehoboam.config import Settings


def test_buy_gate_is_on_the_real_points_scale():
    """The old 30.0 was a 0-100 index value; real-points EP has a median of ~35,
    so an unchanged 30.0 would be almost no filter at all."""
    settings = Settings()
    assert (
        settings.min_expected_points_to_buy != 30.0
    ), "still the old 0-100 index value — re-derive from the v2 distribution"


def test_upgrade_threshold_is_documented_as_real_points():
    field = Settings.model_fields["min_ep_upgrade_threshold"]
    assert "real points" in (field.description or "").lower()


def test_bid_tiers_are_named_constants_not_magic_numbers():
    from rehoboam import bidding_strategy

    assert hasattr(bidding_strategy, "TIER_MUST_HAVE")
    assert hasattr(bidding_strategy, "TIER_STRONG_UPGRADE")
    assert hasattr(bidding_strategy, "TIER_SOLID_UPGRADE")
    assert (
        bidding_strategy.TIER_MUST_HAVE
        > bidding_strategy.TIER_STRONG_UPGRADE
        > bidding_strategy.TIER_SOLID_UPGRADE
    )


def test_trader_scores_with_v2():
    """The live scoring path must call the v2 adapter, not the v1 scorer."""
    import inspect

    from rehoboam import trader

    source = inspect.getsource(trader)
    assert "score_player_v2" in source
    assert "calibration_multiplier" not in source, (
        "REH-20's position calibration was fitted against the old index and "
        "must not be applied to real points"
    )
