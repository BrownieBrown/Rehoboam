"""Every endpoint shares one session; the session retries the statuses a sweep meets."""

from __future__ import annotations

from rehoboam.kickbase_client import KickbaseV4Client


def test_session_retries_429_and_5xx_with_backoff():
    client = KickbaseV4Client()
    retry = client.session.get_adapter("https://api.kickbase.com").max_retries
    assert retry.total == 3
    assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}
    assert retry.backoff_factor == 1.0
    assert retry.respect_retry_after_header is True
    assert retry.raise_on_status is False
    assert "GET" in retry.allowed_methods and "POST" not in retry.allowed_methods
