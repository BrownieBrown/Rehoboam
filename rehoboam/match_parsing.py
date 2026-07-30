"""Shared parsing helpers for Kickbase match records.

Extracted from ``scoring/scorer.py`` so the training corpus and the scorer
agree on what a minutes value means. Behaviour is byte-for-byte the original.
"""

from __future__ import annotations


def parse_minutes(mp) -> int:
    """Parse Kickbase ``mp`` minutes-played values (e.g. ``"13'"``) to int.

    Kickbase ships minutes as a string with a trailing apostrophe.
    Extra-time matches arrive as ``"90+5'"`` per common football
    convention (regulation + stoppage); both components are summed so
    a 95-minute appearance counts as 95, not 0. Anything else (None,
    empty string, future matches without minutes, truly malformed
    entries) degrades silently to 0 — a single odd entry must not
    poison the whole player score.
    """
    if not mp:
        return 0
    s = str(mp).rstrip("'")
    try:
        return int(s)
    except ValueError:
        pass
    if "+" in s:
        try:
            return sum(int(part) for part in s.split("+"))
        except ValueError:
            return 0
    return 0
