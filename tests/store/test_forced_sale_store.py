"""A Top-5 obligation is settled only by a sale that really happened.

A dry run (and a sale that failed) used to write `forced_sales(executed=0)`
on the matchday's primary key, and `forced_sale_settled` counted any row: a
preview on the day an obligation arose cancelled the live forced sale.
"""

from __future__ import annotations


def _record(learner, executed: bool, player_id: str = "p1") -> bool:
    return learner.record_forced_sale(
        matchday=10,
        place=3,
        player_id=player_id,
        player_name=player_id,
        pool="p1,p2",
        reason="top-5 finish",
        executed=executed,
    )


def test_a_dry_run_row_does_not_settle_the_obligation(learner):
    _record(learner, executed=False)

    assert not learner.forced_sale_settled(10)


def test_the_live_sale_replaces_the_preview_and_settles_it(learner):
    _record(learner, executed=False, player_id="previewed")

    assert _record(learner, executed=True, player_id="sold")
    assert learner.forced_sale_settled(10)


def test_an_executed_sale_is_final(learner):
    _record(learner, executed=True, player_id="sold")

    assert not _record(learner, executed=True, player_id="second")
    assert not _record(learner, executed=False, player_id="preview")
    assert learner.forced_sale_settled(10)
