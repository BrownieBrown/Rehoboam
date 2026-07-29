"""Tests for rehoboam.match_parsing."""

from __future__ import annotations

from rehoboam.match_parsing import parse_minutes


def test_plain_minutes():
    assert parse_minutes("67'") == 67


def test_stoppage_time_is_summed():
    # "90+5'" is regulation + stoppage; a 95-minute appearance counts as 95.
    assert parse_minutes("90+5'") == 95


def test_missing_and_malformed_degrade_to_zero():
    assert parse_minutes(None) == 0
    assert parse_minutes("") == 0
    assert parse_minutes("not-a-number") == 0


def test_integer_input_passes_through():
    assert parse_minutes(90) == 90
