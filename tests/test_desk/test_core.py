"""The core book (ADR-0008): one points upgrade a session."""

from __future__ import annotations

from rehoboam.desk import core
from rehoboam.desk.model import Offer, Player

MU = 9 / (10 * 200_000)


def _p(pid, position="Midfielder", ep=60.0, value=5_000_000, drift=0.0):
    return Player(pid, position, market_value=value, ep_next=ep, drift=drift)


def _eleven():
    return (
        [_p("g", "Goalkeeper")]
        + [_p(f"d{i}", "Defender") for i in range(4)]
        + [_p(f"m{i}", "Midfielder") for i in range(4)]
        + [_p(f"f{i}", "Forward") for i in range(2)]
    )


def _offer(pid, ep, arrives=True, value=5_000_000, drift=0.0, sellable=True):
    return Offer(
        _p(pid, "Forward", ep=ep, value=value, drift=drift),
        premium=0.05,
        arrives_before_kickoff=arrives,
        sellable_before_kickoff=sellable,
    )


def test_the_upgrade_that_raises_the_eleven_most_is_bid():
    offers = [_offer("good", 75.0), _offer("star", 120.0)]

    assert (
        core.upgrade(_eleven(), offers, MU, cash=0, allowance=10_000_000).player.player_id == "star"
    )


def test_a_gain_below_the_minimum_is_not_worth_a_premium():
    assert core.upgrade(_eleven(), [_offer("meh", 69.0)], MU, cash=0, allowance=10_000_000) is None


def test_an_upgrade_that_cannot_play_the_next_kickoff_waits():
    late = _offer("star", 120.0, arrives=False)

    assert core.upgrade(_eleven(), [late], MU, cash=0, allowance=10_000_000) is None


def test_the_upgrade_must_fit_the_wallet_and_the_allowance():
    star = _offer("star", 120.0)  # bid 5.25m

    assert core.upgrade(_eleven(), [star], MU, cash=0, allowance=5_000_000) is None
    assert core.upgrade(_eleven(), [star], MU, cash=300_000, allowance=5_000_000) == star


def test_the_displaced_starters_drift_counts_against_the_upgrade():
    from dataclasses import replace

    # Whichever starter the formation drops is worth +10m of drift: more than +15 EP.
    rising = [replace(p, market_value=20_000_000, drift=0.5) for p in _eleven()]
    modest = _offer("modest", 75.0)

    assert core.upgrade(rising, [modest], MU, cash=0, allowance=10_000_000) is None


def test_a_core_that_holds_value_skips_an_upgrade_expected_to_lose_its_premium():
    falling_star = _offer("falling", 120.0, drift=0.02)  # +2% expected, 5% premium
    rising_star = _offer("rising", 100.0, drift=0.08)

    pick = core.upgrade(
        _eleven(), [falling_star, rising_star], MU, cash=0, allowance=10_000_000, holds_value=True
    )

    assert pick == rising_star
    assert core.upgrade(_eleven(), [falling_star], MU, cash=0, allowance=10_000_000) == falling_star


def test_a_capital_neutral_core_pays_at_most_what_the_displaced_starter_sells_for():
    dear = _offer("dear", 120.0, value=9_000_000)  # bid 9.45m against a 5m starter
    cheap = _offer("cheap", 100.0, value=4_500_000)  # bid 4.725m: points bought below his price

    pick = core.upgrade(
        _eleven(), [dear, cheap], MU, cash=0, allowance=20_000_000, capital_neutral=True
    )

    assert pick == cheap
    assert core.upgrade(_eleven(), [dear, cheap], MU, cash=0, allowance=20_000_000) == dear
