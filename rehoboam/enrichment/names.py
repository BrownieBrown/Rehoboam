"""Match an outside source's player name to a Kickbase player on the same club.

Understat writes "Patrik Schick", ligainsider "Schick" or "Luis Díaz"; Kickbase's
universe has `last_name` (and often an empty `first_name`). Names are compared
after stripping accents and case, within one club, and a match is returned only
when exactly one player fits — a second "Müller" on the same club is left
unmatched rather than guessed.
"""

from __future__ import annotations

import unicodedata
from typing import Any

#: Letters NFKD leaves alone because they are letters, not accented ones.
_LIGATURES = {
    "ø": "o",
    "Ø": "O",
    "æ": "ae",
    "Æ": "Ae",
    "œ": "oe",
    "Œ": "Oe",
    "ß": "ss",
    "đ": "d",
    "Đ": "D",
    "ł": "l",
    "Ł": "L",
    "ı": "i",
}


def normalize(name: str | None) -> str:
    """Lower-case ASCII with single spaces: "Luis Díaz" → "luis diaz"."""
    if not name:
        return ""
    text = str(name)
    for src, dst in _LIGATURES.items():
        text = text.replace(src, dst)
    folded = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(ascii_only.lower().replace("-", " ").split())


def _fits(source: str, player: dict[str, Any]) -> bool:
    """The loose test: the surname matches, or the full name does."""
    last = normalize(player.get("last_name"))
    first = normalize(player.get("first_name"))
    if not last:
        return False
    if source == last or source.endswith(" " + last):
        return True
    if source == normalize(f"{first} {last}"):
        return True
    # ligainsider shortens a long or shared name to the first name alone:
    # "Kaishu" (Sano), "Yuito" (Suzuki).
    if first and " " not in source and source == first:
        return True
    # Kickbase's `last_name` is often the display name ("Fábio Silva", "Sambi
    # Lokonga", "Heuer Fernandes") and 518 of 589 rows carry no first name, so
    # a shared token of three letters or more is the loose fit: "Silva",
    # "Sambi", "Kim Min-Jae" against "Kim".
    source_tokens = {t for t in source.split() if len(t) >= 3}
    last_tokens = {t for t in last.split() if len(t) >= 3}
    return bool(source_tokens & last_tokens)


def _fits_strictly(source: str, player: dict[str, Any]) -> bool:
    """The tie-breaker when a club has two players of one surname: the full
    name, or an initial and the surname ("T. Becker")."""
    last = normalize(player.get("last_name"))
    first = normalize(player.get("first_name"))
    if not first or not last:
        return False
    if source == normalize(f"{first} {last}"):
        return True
    tokens = source.replace(".", " ").split()
    return (
        len(tokens) == 2
        and len(tokens[0]) == 1
        and tokens[1] == last
        and first.startswith(tokens[0])
    )


def match_player(
    name: str, team_id: str | None, players_by_team: dict[str, list[dict[str, Any]]]
) -> str | None:
    """The one Kickbase `player_id` on `team_id` whose name fits `name`, else None."""
    source = normalize(name)
    if not source or team_id is None:
        return None
    fits = [p for p in players_by_team.get(str(team_id), []) if _fits(source, p)]
    if len(fits) == 1:
        return str(fits[0]["player_id"])
    if len(fits) > 1:
        strict = [p for p in fits if _fits_strictly(source, p)]
        if len(strict) == 1:
            return str(strict[0]["player_id"])
    return None


def by_team(universe: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for p in universe:
        if p.get("team_id") is not None:
            out.setdefault(str(p["team_id"]), []).append(p)
    return out
