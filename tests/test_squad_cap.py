"""Tests for the 15-player squad cap, counting open bids as committed.

Kickbase counts pending offers toward the 15-player squad cap before they
even resolve. ``_build_session_context`` used to log ``squad=%d/15`` from
``len(squad)`` alone (ignoring ``my_bids``), which could let 13 players plus
3 open bids look like 2 slots open instead of 0 — a 16-player commitment
past the real cap. ``_available_squad_slots`` is the single shared formula
for "room left", used both for that log line and the trade-phase buy gate.
"""

from rehoboam.auto_trader import _available_squad_slots


def test_bid_refused_when_squad_plus_bids_would_reach_cap():
    # 13 players + 2 open bids = 15 already committed — no room for another.
    assert _available_squad_slots(squad_size=13, open_bid_count=2) <= 0


def test_bid_allowed_when_squad_plus_bids_under_cap():
    # 13 players + 1 open bid = 14 committed — one slot open.
    assert _available_squad_slots(squad_size=13, open_bid_count=1) > 0
