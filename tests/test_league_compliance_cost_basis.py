"""The compliance checker's cost basis comes from tracked_purchases, not a JSON file."""

from __future__ import annotations

from types import SimpleNamespace

from rehoboam.bid_learner import BidLearner
from rehoboam.league_compliance import LeagueComplianceChecker


def _player(pid: str, mv: int):
    return SimpleNamespace(id=pid, first_name="A", last_name=pid, market_value=mv)


def test_purchase_below_market_value_is_an_issue(store_dsn):
    learner = BidLearner(dsn=store_dsn)
    learner.add_tracked_purchase(
        player_id="p1", player_name="A p1", buy_price=1_000_000, buy_date=1.0
    )
    api = SimpleNamespace(get_squad=lambda league: [_player("p1", 1_500_000), _player("p2", 9)])
    checker = LeagueComplianceChecker(api, settings=None, learner=learner)
    issues = checker.check_market_value_compliance(league=None)
    assert [i.player_id for i in issues] == ["p1"]
    assert issues[0].violation_amount == 500_000


def test_resolving_an_issue_forgets_the_purchase(store_dsn):
    learner = BidLearner(dsn=store_dsn)
    learner.add_tracked_purchase(player_id="p1", player_name="A p1", buy_price=1, buy_date=1.0)
    listed: list[tuple] = []
    api = SimpleNamespace(
        get_squad=lambda league: [_player("p1", 2)],
        list_player=lambda **kw: listed.append(tuple(sorted(kw.items()))),
    )
    league = SimpleNamespace(id="L")
    checker = LeagueComplianceChecker(api, settings=None, learner=learner)
    sold = checker.resolve_compliance_issues(league, checker.check_market_value_compliance(league))
    assert sold == 1 and len(listed) == 1
    assert learner.get_tracked_purchase("p1") is None
