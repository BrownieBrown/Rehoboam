"""The daily summary email."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rehoboam.notify.email import send_email
from rehoboam.notify.render import render_daily_summary


def _outlook(margin_them=398.0):
    """A MatchupOutlook stand-in; the renderer only reads these attributes."""
    return SimpleNamespace(
        matchup=SimpleNamespace(
            day=1,
            opponent_name="pille",
            starts_at="2026-08-28T18:30:00Z",
            ends_at="2026-08-31T16:00:00Z",
        ),
        us=SimpleNamespace(projected_points=632.0),
        them=SimpleNamespace(projected_points=margin_them),
        margin=632.0 - margin_them,
        verdict="clear favourite",
    )


def _summary(**over):
    base = {
        "outlook": _outlook(),
        "squad_size": 11,
        "budget": 95_317_114,
        "pending": [("Pavlović", 32_608_485)],
        "executed": ["Sold Höler for EUR 3,249,970"],
        "rejections": ["overbid 18.7% exceeds the 8.0% cap"],
        "watch": ["squad 11/15 — no bench"],
    }
    base.update(over)
    return render_daily_summary(**base)


class TestTheFixtureComesFirst:
    """26/27 is head-to-head: one opponent is the entire target."""

    def test_it_names_the_opponent_and_the_matchday(self):
        out = _summary()
        assert "MATCHDAY 1" in out and "pille" in out

    def test_it_shows_both_projections_and_the_margin(self):
        out = _summary()
        assert "632" in out and "398" in out and "+234" in out

    def test_it_states_the_verdict_in_words(self):
        assert "clear favourite" in _summary()

    def test_a_losing_projection_shows_a_negative_margin(self):
        out = _summary(outlook=_outlook(margin_them=700.0))
        assert "-68" in out

    def test_an_unreadable_fixture_omits_the_section_rather_than_faking_it(self):
        out = _summary(outlook=None)
        assert "MATCHDAY" not in out
        assert "SQUAD 11/15" in out


class TestContent:
    def test_it_shows_the_budget(self):
        assert "95,317,114" in _summary()

    def test_it_lists_what_needs_approval(self):
        out = _summary()
        assert "NEEDS YOU (1)" in out and "32,608,485" in out

    def test_the_count_tracks_the_proposals(self):
        out = _summary(pending=[("Pavlović", 32_608_485), ("Lynen", 10_417_894)])
        assert "NEEDS YOU (2)" in out

    def test_no_pending_proposals_says_so_explicitly(self):
        assert "nothing awaiting approval" in _summary(pending=[])

    def test_it_reports_gate_rejections(self):
        """A limit that keeps firing is a signal, not something to hide."""
        assert "8.0% cap" in _summary()

    def test_it_surfaces_watch_items(self):
        assert "no bench" in _summary()

    def test_it_omits_the_watch_section_when_there_is_nothing_to_watch(self):
        assert "WATCH" not in _summary(watch=[])

    def test_it_no_longer_dumps_the_market(self):
        """The market listing was the part nobody acted on."""
        assert "MARKET" not in _summary()


class TestDelivery:
    def test_it_sends_over_smtp(self):
        with patch("rehoboam.notify.email.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value = MagicMock()
            assert (
                send_email(
                    host="h",
                    port=587,
                    user="u",
                    password="p",
                    sender="a@b.c",
                    recipient="d@e.f",
                    subject="s",
                    body="b",
                )
                is True
            )

    def test_a_failure_returns_false_and_does_not_raise(self):
        with patch("rehoboam.notify.email.smtplib.SMTP", side_effect=OSError("down")):
            assert (
                send_email(
                    host="h",
                    port=587,
                    user="u",
                    password="p",
                    sender="a@b.c",
                    recipient="d@e.f",
                    subject="s",
                    body="b",
                )
                is False
            )

    def test_missing_config_returns_false_without_connecting(self):
        with patch("rehoboam.notify.email.smtplib.SMTP") as smtp:
            assert (
                send_email(
                    host="",
                    port=587,
                    user="",
                    password="",
                    sender="",
                    recipient="",
                    subject="s",
                    body="b",
                )
                is False
            )
            smtp.assert_not_called()


class TestSessionCarriesTheLineup:
    def test_the_session_dataclass_defaults_to_an_empty_lineup(self):
        from rehoboam.auto_trader import AutoTradeSession

        s = AutoTradeSession(
            start_time=0.0,
            end_time=1.0,
            profit_trades=[],
            lineup_trades=[],
            errors=[],
            total_spent=0,
            total_earned=0,
            net_change=0,
        )
        assert s.lineup == []


class TestFallbackPathCarriesTheLineup:
    """The EP-pipeline-failure fallback in run_full_session still sets a real
    lineup on Kickbase — it must not report an empty one in the daily email.
    """

    def test_the_ep_pipeline_failure_fallback_returns_the_lineup(self, monkeypatch, tmp_path):
        from rehoboam.auto_trader import AutoTrader
        from rehoboam.config import Settings

        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.chdir(tmp_path)
        trader = AutoTrader(api=MagicMock(), settings=Settings(), dry_run=False)

        sentinel = [("Flekken", 52.0, None)]
        league = SimpleNamespace(id="L", name="L")
        with (
            patch.object(AutoTrader, "_build_session_context", side_effect=RuntimeError("boom")),
            patch.object(AutoTrader, "_set_optimal_lineup", return_value=sentinel),
        ):
            session = trader.run_full_session(league)

        assert session.lineup == sentinel


class TestLockedPathCarriesTheLineup:
    """The matchday-locked early return also sets a real lineup on Kickbase —
    on the days closest to a match, which is the normal daily state rather
    than an edge case — so it must not report an empty one either.
    """

    def test_the_locked_phase_early_return_returns_the_lineup(self, monkeypatch, tmp_path):
        from rehoboam.auto_trader import AutoTrader, EPSessionContext, MatchdayPhase
        from rehoboam.config import Settings

        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.chdir(tmp_path)
        trader = AutoTrader(api=MagicMock(), settings=Settings(), dry_run=False)

        # A minimal fieldable squad (1 GK, 4 DEF, 4 MID, 2 FWD) so
        # `_emergency_slots_short` reports 0 and the locked-phase branch takes
        # the ordinary "no emergency" path, not the emergency-fill path.
        squad = (
            [SimpleNamespace(id="gk0", position="Goalkeeper")]
            + [SimpleNamespace(id=f"def{i}", position="Defender") for i in range(4)]
            + [SimpleNamespace(id=f"mid{i}", position="Midfielder") for i in range(4)]
            + [SimpleNamespace(id=f"fwd{i}", position="Forward") for i in range(2)]
        )
        trader.api.get_squad.return_value = squad

        ctx = EPSessionContext(
            ep_result={},
            matchday_phase=MatchdayPhase(
                days_until_match=1,
                phase="locked",
                max_trades=0,
                allow_flips=False,
                reason="test-locked",
            ),
            my_bids=[],
            my_bid_amounts={},
            squad=squad,
            current_budget=1_000_000,
            team_value=10_000_000,
            flip_budget=0,
        )

        sentinel = [("Flekken", 52.0, None)]
        league = SimpleNamespace(id="L", name="L")
        with (
            patch.object(AutoTrader, "_build_session_context", return_value=ctx),
            patch.object(AutoTrader, "_set_optimal_lineup", return_value=sentinel),
        ):
            session = trader.run_full_session(league)

        assert session.lineup == sentinel
