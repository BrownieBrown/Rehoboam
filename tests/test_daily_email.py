"""The daily summary email."""

from unittest.mock import MagicMock, patch

from rehoboam.notify.email import send_email
from rehoboam.notify.render import render_daily_summary


def _summary(**over):
    base = {
        "lineup": [
            ("Flekken", 52.0, None),
            ("Pavlović", 82.6, None),
            ("Stark", 26.7, "uncertain"),
        ],
        "squad_size": 11,
        "budget": 95_317_114,
        "market": [("Gyamerah", 7_583_425, 25.6), ("Lynen", 10_417_894, 19.7)],
        "pending": [("Pavlović", 32_608_485)],
        "executed": ["Sold Höler for EUR 3,249,970"],
        "rejections": ["overbid 18.7% exceeds the 8.0% cap"],
    }
    base.update(over)
    return render_daily_summary(**base)


class TestContent:
    def test_it_lists_the_lineup_with_ep(self):
        out = _summary()
        assert "Pavlović" in out and "82.6" in out

    def test_it_flags_players_with_a_status_note(self):
        assert "uncertain" in _summary()

    def test_it_shows_the_budget(self):
        assert "95,317,114" in _summary()

    def test_it_lists_pending_proposals(self):
        assert "32,608,485" in _summary()

    def test_it_reports_gate_rejections(self):
        """A limit that keeps firing is a signal, not something to hide."""
        assert "8.0% cap" in _summary()

    def test_it_reports_proposal_volume_for_approval_fatigue(self):
        assert "PENDING PROPOSALS (1)" in _summary()

    def test_the_count_tracks_the_proposals(self):
        out = _summary(pending=[("Pavlović", 32_608_485), ("Lynen", 10_417_894)])
        assert "PENDING PROPOSALS (2)" in out


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
