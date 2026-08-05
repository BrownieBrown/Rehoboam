"""REH-71: model the live bot's profit-flip BUYS inside the replay.

The replay already models profit-taking sells (`engine._flip_sells`). The live
bot also buys purely for expected appreciation (`auto_trader.py:342-392` ->
`Trader.find_profit_opportunities` -> `ProfitTrader`), and the real
-EUR 55.3M came from both halves. Deciding the flip policy from the sell half
alone answers a question nobody asked.
"""

from __future__ import annotations

import ast
import inspect
import sqlite3
import textwrap

from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.flip_buys import (
    CorpusMarketPlayer,
    average_points_at,
    flip_bid_ceiling,
    history_at,
)


def _attributes_read_off(name: str, *functions) -> set[str]:
    """Every `<name>.<attr>` read anywhere in the given functions."""
    found: set[str] = set()
    for fn in functions:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        found |= {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == name
        }
    return found


def test_the_adapter_satisfies_every_attribute_profit_trader_reads():
    """A contract test, not a formality. ProfitTrader is shipped code this
    module does not own. If it grows a new attribute read, the replay would
    raise AttributeError deep inside a season run, most likely on one matchday
    of thirty-four. Fail here instead.
    """
    from rehoboam.profit_trader import ProfitTrader

    read = _attributes_read_off(
        "player",
        ProfitTrader.find_profit_opportunities,
        ProfitTrader._calculate_risk,
    )

    missing = read - set(CorpusMarketPlayer.__dataclass_fields__)
    assert not missing, f"ProfitTrader reads attributes the adapter lacks: {missing}"


DAY = 86400.0


def _corpus_with_mv(tmp_path, series: list[tuple[float, int]]) -> TrainingCorpus:
    corpus = TrainingCorpus(tmp_path / "corpus.db")
    with sqlite3.connect(corpus.db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO mv_series (player_id, snapshot_at, market_value) "
            "VALUES ('p1', ?, ?)",
            series,
        )
        conn.commit()
    return corpus


def test_history_stops_strictly_before_the_decision_time(tmp_path):
    corpus = _corpus_with_mv(tmp_path, [(1 * DAY, 100), (2 * DAY, 200), (3 * DAY, 300)])

    history = history_at(corpus, "p1", 3 * DAY)

    assert [item["mv"] for item in history["it"]] == [100, 200]


def test_a_future_spike_cannot_reach_the_peak(tmp_path):
    """The leak that matters. ProfitTrader's mean-reversion branch gates on
    `current_vs_peak_pct < -25` (profit_trader.py:172-175). A season-wide peak
    would let the bot know in August what a player is worth in March.
    """
    from rehoboam.services.trend_service import TrendService

    corpus = _corpus_with_mv(tmp_path, [(1 * DAY, 100), (2 * DAY, 110), (9 * DAY, 10_000)])

    analysis = TrendService.analyze(history_at(corpus, "p1", 3 * DAY), 110)

    assert analysis.peak_value == 110


def test_days_since_epoch_round_trips_exactly(tmp_path):
    """`record_mv_series` stores `dt * 86400`; `analyze` sorts on `dt`. A lossy
    round trip would silently reorder the series."""
    corpus = _corpus_with_mv(tmp_path, [(7 * DAY, 500)])

    assert history_at(corpus, "p1", 8 * DAY)["it"] == [{"dt": 7, "mv": 500}]


SEASON = "2025/2026"


def _corpus_with_matches(tmp_path, rows: list[tuple[int, int, int]]) -> TrainingCorpus:
    """rows: (day_number, points, status)."""
    corpus = TrainingCorpus(tmp_path / "corpus.db")
    with sqlite3.connect(corpus.db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO player_match_history "
            "(player_id, season, day_number, points, minutes, is_home, status) "
            "VALUES ('p1', ?, ?, ?, 90, 1, ?)",
            [(SEASON, day, points, status) for day, points, status in rows],
        )
        conn.commit()
    return corpus


def test_average_points_ignores_matchdays_at_or_after_the_cutoff(tmp_path):
    corpus = _corpus_with_matches(tmp_path, [(1, 100, 5), (2, 200, 5), (3, 900, 5)])

    assert average_points_at(corpus, "p1", season=SEASON, day_number=3) == 150.0


def test_only_appearances_count_towards_the_average(tmp_path):
    """Kickbase's own average is per appearance. Averaging in matchdays the
    player was not in the squad (status 1) or sat unused (status 4) drags a fit
    player under ProfitTrader's MIN_AVG_POINTS gate of 20 and silently removes
    him from every flip branch.
    """
    corpus = _corpus_with_matches(tmp_path, [(1, 60, 5), (2, 0, 1), (3, 0, 4)])

    assert average_points_at(corpus, "p1", season=SEASON, day_number=4) == 60.0


def test_a_player_with_no_appearances_averages_zero(tmp_path):
    corpus = _corpus_with_matches(tmp_path, [(1, 0, 1)])

    assert average_points_at(corpus, "p1", season=SEASON, day_number=2) == 0.0


def test_the_ceiling_leaves_the_required_margin_intact():
    """Buy at the ceiling, sell at the appreciated value, and the round trip
    still returns exactly min_profit_pct."""
    ceiling = flip_bid_ceiling(10_000_000, 20.0, min_profit_pct=8.0)

    exit_value = 10_000_000 * 1.20
    realised_pct = (exit_value - ceiling) / ceiling * 100

    assert abs(realised_pct - 8.0) < 0.01


def test_a_bigger_expected_move_justifies_a_bigger_bid():
    """The gradient must discriminate across the operating range -- the missing
    test class that let REH-69 ship a saturated bid function."""
    low = flip_bid_ceiling(10_000_000, 10.0, min_profit_pct=8.0)
    mid = flip_bid_ceiling(10_000_000, 20.0, min_profit_pct=8.0)
    high = flip_bid_ceiling(10_000_000, 40.0, min_profit_pct=8.0)

    assert low < mid < high


def test_a_flip_with_no_expected_upside_bids_below_market_value():
    """Otherwise the bot pays full price for a player it expects to stagnate."""
    assert flip_bid_ceiling(10_000_000, 0.0, min_profit_pct=8.0) < 10_000_000
