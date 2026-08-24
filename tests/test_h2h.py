"""Head-to-head matchup awareness.

26/27 moved from total points to H2H, so the target is no longer "score as
much as possible" but "score more than one specific opponent".
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from rehoboam.h2h import matchup_outlook, next_matchup, project_squad

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _api(matchups=None, squads=None, user_id="3616202"):
    api = MagicMock()
    api.user.id = user_id
    api.client.BASE_URL = "https://x"

    def _get(url):
        resp = MagicMock()
        if url.endswith("/matchups"):
            resp.json.return_value = matchups or {}
        else:
            manager = url.rsplit("/managers/", 1)[-1].split("/")[0]
            resp.json.return_value = {"it": (squads or {}).get(manager, [])}
        return resp

    api.client.session.get.side_effect = _get
    return api


def _md(day, ends, u1, u2):
    return {
        "day": day,
        "sd": "2026-08-28T18:30:00Z",
        "ed": ends,
        "it": [{"u1": {"i": u1, "n": u1}, "u2": {"i": u2, "n": u2}}],
    }


class TestFindingTheNextFixture:
    def test_it_finds_our_opponent(self):
        api = _api({"mds": [_md(1, "2026-08-31T16:00:00Z", "3616202", "pille")]})
        m = next_matchup(api, SimpleNamespace(id="L"), now=NOW)
        assert m.opponent_name == "pille"
        assert m.day == 1

    def test_it_works_when_we_are_the_second_named_manager(self):
        api = _api({"mds": [_md(1, "2026-08-31T16:00:00Z", "pille", "3616202")]})
        assert next_matchup(api, SimpleNamespace(id="L"), now=NOW).opponent_name == "pille"

    def test_it_skips_finished_matchdays(self):
        api = _api(
            {
                "mds": [
                    _md(1, "2026-08-10T16:00:00Z", "3616202", "past"),
                    _md(2, "2026-08-31T16:00:00Z", "3616202", "next"),
                ]
            }
        )
        assert next_matchup(api, SimpleNamespace(id="L"), now=NOW).opponent_name == "next"

    def test_an_in_progress_matchday_is_still_the_next_one(self):
        """Mid-matchday, that opponent is still who the lineup faces."""
        api = _api({"mds": [_md(1, "2026-08-25T16:00:00Z", "3616202", "live")]})
        assert next_matchup(api, SimpleNamespace(id="L"), now=NOW).opponent_name == "live"

    def test_a_fixture_we_are_not_in_is_ignored(self):
        api = _api({"mds": [_md(1, "2026-08-31T16:00:00Z", "a", "b")]})
        assert next_matchup(api, SimpleNamespace(id="L"), now=NOW) is None

    def test_an_unreadable_schedule_returns_none_rather_than_raising(self):
        api = MagicMock()
        api.user.id = "3616202"
        api.client.BASE_URL = "https://x"
        api.client.session.get.side_effect = OSError("down")
        assert next_matchup(api, SimpleNamespace(id="L"), now=NOW) is None


def _p(name, pos, ap, st=0):
    return {"pn": name, "pos": pos, "ap": ap, "st": st}


class TestProjectingASquad:
    def test_it_fields_eleven_and_sums_their_averages(self):
        rows = [_p(f"p{i}", 3, 10.0) for i in range(9)]
        rows += [_p("gk", 1, 5.0), _p("d1", 2, 5.0), _p("d2", 2, 5.0), _p("d3", 2, 5.0)]
        proj = project_squad(rows, "them")
        assert len(proj.eleven) == 11
        assert proj.projected_points == sum(p[2] for p in proj.eleven)

    def test_it_respects_position_minimums(self):
        """Eleven midfielders is not a legal eleven, however well they score."""
        rows = [_p(f"m{i}", 3, 100.0) for i in range(11)]
        rows += [
            _p("gk", 1, 1.0),
            _p("d1", 2, 1.0),
            _p("d2", 2, 1.0),
            _p("d3", 2, 1.0),
            _p("f1", 4, 1.0),
        ]
        proj = project_squad(rows, "them")
        positions = [p[1] for p in proj.eleven]
        assert positions.count("Goalkeeper") >= 1
        assert positions.count("Defender") >= 3
        assert positions.count("Forward") >= 1

    def test_injured_players_are_excluded(self):
        """They cannot be fielded, so counting them flatters that side."""
        healthy = [_p(f"p{i}", 3, 10.0) for i in range(11)]
        proj_healthy = project_squad(healthy, "them")
        with_injured = healthy + [_p("broken", 3, 500.0, st=256)]
        assert project_squad(with_injured, "them").projected_points == (
            proj_healthy.projected_points
        )

    def test_an_empty_squad_projects_zero_rather_than_raising(self):
        assert project_squad([], "them").projected_points == 0.0


class TestTheOutlook:
    def _outlook(self, our_pts, their_pts):
        squads = {
            "3616202": [_p(f"u{i}", 3, our_pts / 11) for i in range(11)],
            "pille": [_p(f"t{i}", 3, their_pts / 11) for i in range(11)],
        }
        api = _api({"mds": [_md(1, "2026-08-31T16:00:00Z", "3616202", "pille")]}, squads)
        return matchup_outlook(api, SimpleNamespace(id="L"), now=NOW)

    def test_a_big_lead_reads_as_clear_favourite(self):
        o = self._outlook(700, 400)
        assert o.margin > 0
        assert o.verdict == "clear favourite"

    def test_a_narrow_gap_reads_as_too_close_to_call(self):
        assert self._outlook(700, 690).verdict == "too close to call"

    def test_being_well_behind_reads_as_heavy_underdog(self):
        o = self._outlook(400, 700)
        assert o.margin < 0
        assert o.verdict == "heavy underdog"

    def test_no_fixture_means_no_outlook(self):
        api = _api({"mds": []})
        assert matchup_outlook(api, SimpleNamespace(id="L"), now=NOW) is None
