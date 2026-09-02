"""Tell Marco when a player he wants comes within reach (REH-119).

Olise, 2026-09-02: asking EUR 64,976,131 against a 33% cap of EUR 51,117,765.
Unbuyable, and the only way to discover that was to try. He is a season-long
target, so the useful thing is not a refusal at bid time — it is knowing which
way the gap is moving and when it closes.

The gap closes by GROWING total worth, never by selling: a sale moves money
from team value to budget and leaves worth unchanged (REH-118). So the line
reports the shortfall in worth, which is the number that has to move.
"""

from __future__ import annotations

import pytest

from rehoboam.notify.watch import WatchTarget, parse_watch_ids, render_watch_line

OLISE = WatchTarget(player_id="8329", name="Olise", ask=64_976_131)


class TestTheOliseCase:
    def test_it_reports_how_far_short_we_are(self):
        line = render_watch_line(OLISE, total_worth=154_902_319, max_pct=33.0)

        assert "Olise" in line
        assert "64,976,131" in line
        # worth needed = ceil(ask / 0.33) = 196,897,367; shortfall = 41,995,048
        assert "41,995,048" in line, line

    def test_it_says_he_is_out_of_reach(self):
        line = render_watch_line(OLISE, total_worth=154_902_319, max_pct=33.0)

        assert "out of reach" in line.lower() or "short" in line.lower()

    def test_it_flips_to_affordable_once_worth_is_enough(self):
        line = render_watch_line(OLISE, total_worth=200_000_000, max_pct=33.0)

        assert "affordable" in line.lower() or "within reach" in line.lower()
        assert "short" not in line.lower()

    def test_the_boundary_is_inclusive(self):
        worth = 196_897_367  # ask / 0.33, rounded up
        line = render_watch_line(OLISE, total_worth=worth, max_pct=33.0)

        assert "short" not in line.lower(), line


class TestUnknownWorth:
    def test_it_does_not_pretend_to_know(self):
        line = render_watch_line(OLISE, total_worth=None, max_pct=33.0)

        assert "unknown" in line.lower()


class TestParsingTheSetting:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("8329", ["8329"]),
            ("8329,1685", ["8329", "1685"]),
            (" 8329 , 1685 ", ["8329", "1685"]),
            ("", []),
            (None, []),
            ("8329,,1685", ["8329", "1685"]),
        ],
    )
    def test_it_parses_the_env_list(self, raw, expected):
        assert parse_watch_ids(raw) == expected
