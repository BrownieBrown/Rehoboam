"""Telegram delivery.

Notification failures must NEVER block trading — the project's existing
best-effort pattern. These tests pin that a dead Telegram is survivable.
"""

import logging
from unittest.mock import MagicMock, patch

from rehoboam.notify.telegram import send_proposal


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
