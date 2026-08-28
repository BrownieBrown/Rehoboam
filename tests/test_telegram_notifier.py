"""Telegram delivery.

Notification failures must NEVER block trading — the project's existing
best-effort pattern. These tests pin that a dead Telegram is survivable.
"""

import logging
from unittest.mock import MagicMock, patch

from rehoboam.notify.telegram import MAX_APPROVAL_BUTTONS, send_message, send_proposal


class TestSuccess:
    def test_it_posts_to_the_send_message_endpoint(self):
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            assert send_proposal("T", "C", "p1", "hello") is True
        url = post.call_args[0][0]
        assert "api.telegram.org/botT/sendMessage" in url

    def test_it_attaches_approve_and_reject_buttons_carrying_the_id(self):
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            send_proposal("T", "C", "p1", "hello")
        markup = post.call_args[1]["json"]["reply_markup"]
        data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
        assert "approve:p1" in data
        assert "reject:p1" in data


class TestFailuresAreSurvivable:
    def test_a_non_200_returns_false_and_does_not_raise(self):
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=500)
            assert send_proposal("T", "C", "p1", "hello") is False

    def test_a_network_error_returns_false_and_does_not_raise(self):
        with patch("rehoboam.notify.telegram.requests.post", side_effect=OSError("down")):
            assert send_proposal("T", "C", "p1", "hello") is False

    def test_missing_credentials_return_false_without_calling_out(self):
        with patch("rehoboam.notify.telegram.requests.post") as post:
            assert send_proposal("", "", "p1", "hello") is False
            post.assert_not_called()

    def test_a_network_error_does_not_leak_the_token_into_the_log(self, caplog):
        boom = OSError(
            "HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries "
            "exceeded with url: /botSECRET123/sendMessage"
        )
        with patch("rehoboam.notify.telegram.requests.post", side_effect=boom):
            with caplog.at_level(logging.WARNING):
                assert send_proposal("SECRET123", "C", "p1", "hello") is False
        assert "SECRET123" not in caplog.text


class TestPlainMessages:
    """The daily summary goes over Telegram rather than SMTP.

    Proton needs a paid plan plus a custom domain, and Proton Bridge only
    binds to localhost so an Azure Function can never reach it. Telegram is
    already configured and verified, so the summary reuses it.
    """

    def test_it_sends_a_plain_message_without_buttons(self):
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            assert send_message("T", "C", "hello") is True
        payload = post.call_args[1]["json"]
        assert payload["text"] == "hello"
        assert "reply_markup" not in payload

    def test_missing_credentials_return_false_without_calling_out(self):
        with patch("rehoboam.notify.telegram.requests.post") as post:
            assert send_message("", "", "hello") is False
            post.assert_not_called()

    def test_a_failure_returns_false_and_does_not_raise(self):
        with patch("rehoboam.notify.telegram.requests.post", side_effect=OSError("down")):
            assert send_message("T", "C", "hello") is False


class TestApprovalButtonsOnTheSummary:
    """REH-106: the message Marco actually reads each morning had no button.

    `send_proposal` carries buttons, but it fires once, at proposal creation.
    The daily summary — the message that arrives every day and lists what is
    awaiting approval — went out as plain text reading "approve <name>" with
    no way to do it. On locked matchdays no new proposal is created at all, so
    days passed with only button-less summaries. Proposals sat at `pending`
    while rivals bought the players out from under them.
    """

    def test_pending_proposals_become_approve_and_reject_buttons(self):
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            send_message("T", "C", "summary", approvals=[("p1", "Okpala")])
        markup = post.call_args[1]["json"]["reply_markup"]
        data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
        assert "approve:p1" in data
        assert "reject:p1" in data

    def test_the_buttons_ride_on_the_last_chunk_only(self):
        """A split summary must not repeat the keyboard on every part.

        Duplicated keyboards mean the same proposal is tappable from several
        messages; the second tap gets "already <status>" and reads as a bug.
        The buttons belong on the message that ends the summary.
        """
        text = "\n".join(f"line {i} " + "x" * 80 for i in range(200))
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            send_message("T", "C", text, approvals=[("p1", "Okpala")])
        payloads = [c[1]["json"] for c in post.call_args_list]
        assert len(payloads) > 1
        assert all("reply_markup" not in p for p in payloads[:-1])
        assert "reply_markup" in payloads[-1]

    def test_a_long_pending_queue_keeps_the_newest_proposals(self):
        """Nothing reaps a stale proposal yet (REH-108), so the queue grows.

        A wall of buttons is unusable and risks Telegram refusing the markup
        outright. Which end to drop is a domain call, not a formatting one:
        measured poach latency is hours (Ndiaye 2h48m, Awortwie-Grant 8h30m),
        so an old pending proposal is almost certainly dead while a fresh one
        is still winnable. `pending_proposals()` is oldest-first, so the cap
        keeps the tail.
        """
        approvals = [(f"p{i}", f"Player{i}") for i in range(25)]
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            send_message("T", "C", "summary", approvals=approvals)
        markup = post.call_args[1]["json"]["reply_markup"]
        rows = markup["inline_keyboard"]
        assert len(rows) == MAX_APPROVAL_BUTTONS
        data = [b["callback_data"] for row in rows for b in row]
        assert "approve:p24" in data, "newest proposal must be actionable"
        assert "approve:p0" not in data, "oldest is dropped, not the newest"


class TestLongMessagesAreSplit:
    """Telegram rejects anything over 4096 characters.

    A summary that grew past the cap must not vanish, and must not be cut
    through the middle of a number.
    """

    def test_a_long_message_is_split_on_line_boundaries(self):
        text = "\n".join(f"line {i} " + "x" * 80 for i in range(200))
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            assert send_message("T", "C", text) is True
        sent = [c[1]["json"]["text"] for c in post.call_args_list]
        assert len(sent) > 1
        assert all(len(s) <= 4096 for s in sent)
        # nothing lost, and no line torn in half
        assert "\n".join(sent).split("\n") == text.split("\n")

    def test_a_short_message_is_sent_as_one(self):
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            send_message("T", "C", "short")
        assert post.call_count == 1

    def test_a_long_proposal_is_split_and_still_carries_its_buttons(self):
        """`send_proposal` never chunked, unlike `send_message`.

        Telegram rejects a message over the cap with a 400, so an oversized
        proposal was never delivered at all — the decision existed only as a
        row in `trade_proposals` that nobody could see or approve. Only a
        `logger.warning` recorded it.
        """
        text = "\n".join(f"line {i} " + "x" * 80 for i in range(200))
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            assert send_proposal("T", "C", "p1", text) is True
        payloads = [c[1]["json"] for c in post.call_args_list]
        assert len(payloads) > 1
        assert all(len(p["text"]) <= 4096 for p in payloads)
        assert "reply_markup" in payloads[-1], "the tap target must survive splitting"
        assert all("reply_markup" not in p for p in payloads[:-1])

    def test_one_failed_chunk_makes_the_whole_send_false(self):
        text = "\n".join("y" * 200 for _ in range(60))
        with patch("rehoboam.notify.telegram.requests.post") as post:
            post.side_effect = [MagicMock(status_code=200), MagicMock(status_code=500)]
            assert send_message("T", "C", text) is False
