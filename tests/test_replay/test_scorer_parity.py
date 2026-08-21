"""REH-84: the replay must score through the SAME function the live bot uses.

`replay/driver.py`'s `score` re-implemented `compose_ep`'s body inline. The two
agreed until REH-80 added a cold-start discount inside `compose_ep` -- after
which the replay silently kept scoring unfitted players the old way, and a
season replay run before and after that change printed an identical 26,960
points. A harness that cannot see a scorer change will report "no regression"
for every scorer bug ever shipped.

`scoring/v2/thresholds.py` already states the rule this pins:
"A second implementation would drift."
"""

from __future__ import annotations

import sqlite3

from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.driver import SEASON, _make_score_fn
from rehoboam.scoring.v2.adapter import compose_ep
from rehoboam.scoring.v2.coefficients import load_coefficients

# Deliberately absent from the fitted coefficients, so the cold-start path runs.
UNFITTED = "no-such-player-in-coefficients"
DAY0 = 1_700_000_000.0


def _corpus(tmp_path) -> TrainingCorpus:
    corpus = TrainingCorpus(tmp_path / "corpus.db")
    with sqlite3.connect(corpus.db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO player_match_history "
            "(player_id, season, day_number, points, minutes, is_home, status) "
            "VALUES (?, ?, ?, ?, 90, 1, ?)",
            [(UNFITTED, SEASON, day, 60, 5) for day in (1, 2)],
        )
        conn.commit()
    return corpus


def test_the_replay_scores_an_unfitted_player_exactly_as_the_live_bot_does(tmp_path):
    """The regression: before REH-84 the replay returned the UNDISCOUNTED prior
    for an unfitted player, so it was blind to REH-80 entirely."""
    corpus = _corpus(tmp_path)
    kickoffs = {1: DAY0, 2: DAY0 + 7 * 86400, 3: DAY0 + 14 * 86400}
    score = _make_score_fn(corpus, SEASON, kickoffs)

    availability, rate, _ = load_coefficients()
    assert UNFITTED not in rate.quality, "fixture must exercise the cold-start path"

    # Decision instant: matchday 3, so matches 1 and 2 are visible; the last
    # played status is 5.
    replayed = score(UNFITTED, kickoffs[3])
    live = compose_ep(UNFITTED, 5, None, availability, rate)

    assert replayed == live


def test_the_replay_scores_a_fitted_player_exactly_as_the_live_bot_does(tmp_path):
    """Parity must hold on the ordinary path too, not only the cold-start one."""
    availability, rate, _ = load_coefficients()
    fitted = next(iter(rate.quality))

    corpus = TrainingCorpus(tmp_path / "corpus.db")
    with sqlite3.connect(corpus.db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO player_match_history "
            "(player_id, season, day_number, points, minutes, is_home, status) "
            "VALUES (?, ?, ?, ?, 90, 1, ?)",
            [(fitted, SEASON, day, 60, 5) for day in (1, 2)],
        )
        conn.commit()

    kickoffs = {1: DAY0, 2: DAY0 + 7 * 86400, 3: DAY0 + 14 * 86400}
    score = _make_score_fn(corpus, SEASON, kickoffs)

    position = corpus.positions_for([fitted]).get(fitted)
    assert score(fitted, kickoffs[3]) == compose_ep(fitted, 5, position, availability, rate)
