"""Formation and squad validation"""

from dataclasses import dataclass


@dataclass
class FormationRequirements:
    """Formation requirements for Bundesliga fantasy"""

    min_goalkeepers: int = 1
    min_defenders: int = 3
    min_midfielders: int = 2
    min_forwards: int = 1
    max_squad_size: int = 15
    starting_eleven_size: int = 11


POSITION_MAPPING = {"Goalkeeper": "GK", "Defender": "DEF", "Midfielder": "MID", "Forward": "FWD"}

#: Formations Kickbase accepts, as (defenders, midfielders, forwards); one
#: goalkeeper is implicit. This is the API's own list: `GET /v4/config` returns
#: `cps[*].lts`, the same ten for every eleven-a-side competition (`lpc == 11`;
#: the six-a-side modes list 2-1-2 / 1-2-2 / 2-2-1 and do not apply),
#: read-only-probed on 2026-09-11. `scripts/probe_formations.py` re-verifies it
#: against the live API and exits non-zero on a mismatch — run it if Kickbase
#: ever answers a lineup call with `LineupNotEnoughPlayers` on a formation
#: listed here.
#: Guessing in either direction costs points: a formation missing here is an
#: eleven we never field (3-6-1 and 4-2-4 were missing until this list came
#: from the API), and one listed here that Kickbase rejects is a lineup that
#: is never set at all.
LEGAL_FORMATIONS: frozenset[tuple[int, int, int]] = frozenset(
    {
        (3, 4, 3),
        (3, 5, 2),
        (3, 6, 1),
        (4, 2, 4),
        (4, 3, 3),
        (4, 4, 2),
        (4, 5, 1),
        (5, 2, 3),
        (5, 3, 2),
        (5, 4, 1),
    }
)

# Maximum players per position that can ever start across the legal
# formations. Derived, so the two can never disagree. Used to detect "dead
# weight" — a player who can never enter any starting 11 because the position
# is already saturated.
_POSITION_MAX_STARTERS = {
    "Goalkeeper": 1,
    "Defender": max(d for d, _, _ in LEGAL_FORMATIONS),
    "Midfielder": max(m for _, m, _ in LEGAL_FORMATIONS),
    "Forward": max(f for _, _, f in LEGAL_FORMATIONS),
}

_POSITION_ORDER = ("Goalkeeper", "Defender", "Midfielder", "Forward")


@dataclass(frozen=True)
class Fieldability:
    """Can these players field a legal eleven, and if not, what would it take?

    ``purchases`` is the smallest number of players to buy so that some legal
    formation fits. ``positions`` is the union of positions across every plan
    of that minimal size — any one of them is an acceptable next buy. ``ok``
    is ``purchases == 0``. ``reason`` is human-readable and stable enough for
    logs: "Only N available players, need 11", "<Position>: have X, need Y",
    or "Only N of M can start in any formation (<Position> X > ceiling)".
    """

    ok: bool
    reason: str
    counts: dict[str, int]
    purchases: int
    positions: frozenset[str]


def _shortfall(counts: dict[str, int], formation: tuple[int, int, int]) -> dict[str, int]:
    d, m, f = formation
    need = {"Goalkeeper": 1, "Defender": d, "Midfielder": m, "Forward": f}
    return {pos: max(0, n - counts.get(pos, 0)) for pos, n in need.items()}


def fieldability_from_counts(counts: dict[str, int]) -> Fieldability:
    """Answer fieldability from position counts alone.

    Pure on counts so callers can ask "and after buying a midfielder?" by
    adding one to a copy — the emergency basket does exactly that.
    """
    requirements = FormationRequirements()
    plans = [_shortfall(counts, formation) for formation in LEGAL_FORMATIONS]
    purchases = min(sum(plan.values()) for plan in plans)
    positions = frozenset(
        pos for plan in plans if sum(plan.values()) == purchases for pos, n in plan.items() if n > 0
    )
    available = sum(counts.get(pos, 0) for pos in _POSITION_ORDER)

    if purchases == 0:
        reason = "Legal starting 11 available"
    elif available < requirements.starting_eleven_size:
        reason = f"Only {available} available players, need {requirements.starting_eleven_size}"
    else:
        minimums = {
            "Goalkeeper": requirements.min_goalkeepers,
            "Defender": requirements.min_defenders,
            "Midfielder": requirements.min_midfielders,
            "Forward": requirements.min_forwards,
        }
        unmet = next((pos for pos in _POSITION_ORDER if counts.get(pos, 0) < minimums[pos]), None)
        if unmet is not None:
            reason = f"{unmet}: have {counts.get(unmet, 0)}, need {minimums[unmet]}"
        else:
            over = ", ".join(
                f"{pos} {counts.get(pos, 0)} > {ceiling}"
                for pos, ceiling in _POSITION_MAX_STARTERS.items()
                if counts.get(pos, 0) > ceiling
            )
            fieldable = sum(
                min(counts.get(pos, 0), ceiling) for pos, ceiling in _POSITION_MAX_STARTERS.items()
            )
            reason = (
                f"Only {fieldable} of {available} can start in any formation ({over}); "
                f"need {', '.join(sorted(positions))}"
            )

    return Fieldability(
        ok=purchases == 0,
        reason=reason,
        counts={pos: counts.get(pos, 0) for pos in _POSITION_ORDER},
        purchases=purchases,
        positions=positions,
    )


def fieldability(available: list) -> Fieldability:
    """`fieldability_from_counts` over a list of players (uses ``.position``)."""
    return fieldability_from_counts(get_position_counts(available))


def is_legal_formation(players: list) -> bool:
    """True when ``players`` is exactly eleven in a formation Kickbase accepts."""
    if len(players) != FormationRequirements().starting_eleven_size:
        return False
    counts = get_position_counts(players)
    if counts["Goalkeeper"] != 1:
        return False
    return (
        counts["Defender"],
        counts["Midfielder"],
        counts["Forward"],
    ) in LEGAL_FORMATIONS


def get_position_counts(players: list) -> dict[str, int]:
    """Count players by position"""
    counts = {"Goalkeeper": 0, "Defender": 0, "Midfielder": 0, "Forward": 0}

    for player in players:
        position = player.position if hasattr(player, "position") else "Unknown"
        if position in counts:
            counts[position] += 1

    return counts


def validate_formation(players: list, requirements: FormationRequirements = None) -> dict[str, any]:
    """
    Validate if squad meets formation requirements

    Returns:
        dict with:
            - valid: bool
            - issues: list of issues
            - position_counts: dict of position counts
            - can_field_eleven: bool
    """
    if requirements is None:
        requirements = FormationRequirements()

    counts = get_position_counts(players)
    issues = []

    # Check minimum requirements
    if counts["Goalkeeper"] < requirements.min_goalkeepers:
        issues.append(f"Need {requirements.min_goalkeepers} GK, have {counts['Goalkeeper']}")

    if counts["Defender"] < requirements.min_defenders:
        issues.append(f"Need {requirements.min_defenders} DEF, have {counts['Defender']}")

    if counts["Midfielder"] < requirements.min_midfielders:
        issues.append(f"Need {requirements.min_midfielders} MID, have {counts['Midfielder']}")

    if counts["Forward"] < requirements.min_forwards:
        issues.append(f"Need {requirements.min_forwards} FWD, have {counts['Forward']}")

    # Check squad size
    total_players = sum(counts.values())
    if total_players > requirements.max_squad_size:
        issues.append(f"Squad too large: {total_players}/{requirements.max_squad_size}")

    can_field_eleven = total_players >= requirements.starting_eleven_size and len(issues) == 0

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "position_counts": counts,
        "total_players": total_players,
        "can_field_eleven": can_field_eleven,
    }


def select_best_eleven(squad: list, player_values: dict[str, float]) -> list:
    """Select the best starting eleven that Kickbase will accept.

    Tries every legal formation: the top goalkeeper plus the top ``d`` / ``m``
    / ``f`` players at each position by ``player_values``, and keeps the
    formation with the highest total. Exact, and only ten formations wide.

    When no legal formation fits (too few bodies, or a position over its
    ceiling with nothing to fill the rest), falls back to the greedy partial
    list the bot has always produced, so callers that reason about a
    short squad — replay, backtest, marginal-EP — keep their behaviour. The
    lineup step checks ``is_legal_formation`` before submitting.
    """
    by_position: dict[str, list] = {pos: [] for pos in _POSITION_ORDER}
    ranked = sorted(squad, key=lambda p: player_values.get(p.id, 0), reverse=True)
    for player in ranked:
        if player.position in by_position:
            by_position[player.position].append(player)

    best: list | None = None
    best_total = float("-inf")
    for d, m, f in sorted(LEGAL_FORMATIONS):
        need = {"Goalkeeper": 1, "Defender": d, "Midfielder": m, "Forward": f}
        if any(len(by_position[pos]) < n for pos, n in need.items()):
            continue
        eleven = [p for pos in _POSITION_ORDER for p in by_position[pos][: need[pos]]]
        total = sum(player_values.get(p.id, 0) for p in eleven)
        if total > best_total:
            best, best_total = eleven, total
    if best is not None:
        return best
    return _greedy_partial_eleven(squad, player_values)


def _greedy_partial_eleven(squad: list, player_values: dict[str, float]) -> list:
    """
    Select the best starting 11 from squad based on value scores

    Fallback for squads that cannot field a legal eleven; returns fewer than
    eleven.

    Args:
        squad: List of players
        player_values: Dict mapping player.id -> value_score

    Returns:
        List of 11 best players that satisfy formation requirements
    """
    requirements = FormationRequirements()

    # Sort by value score (highest first)
    sorted_squad = sorted(squad, key=lambda p: player_values.get(p.id, 0), reverse=True)

    # Greedy selection: pick best players while satisfying formation
    selected = []
    position_counts = {"Goalkeeper": 0, "Defender": 0, "Midfielder": 0, "Forward": 0}

    # First pass: ensure minimum requirements
    for player in sorted_squad:
        pos = player.position

        # Check if we need this position for minimum requirements
        if pos == "Goalkeeper" and position_counts[pos] < requirements.min_goalkeepers:
            selected.append(player)
            position_counts[pos] += 1
        elif pos == "Defender" and position_counts[pos] < requirements.min_defenders:
            selected.append(player)
            position_counts[pos] += 1
        elif pos == "Midfielder" and position_counts[pos] < requirements.min_midfielders:
            selected.append(player)
            position_counts[pos] += 1
        elif pos == "Forward" and position_counts[pos] < requirements.min_forwards:
            selected.append(player)
            position_counts[pos] += 1

        if len(selected) >= requirements.starting_eleven_size:
            break

    # Second pass: fill remaining spots with best available, but respect
    # position ceilings.  A 2nd GK can never play in any formation, so
    # picking one here would block a useful outfield player and break
    # marginal-EP calculations downstream.
    if len(selected) < requirements.starting_eleven_size:
        for player in sorted_squad:
            if player not in selected:
                pos = player.position
                max_at_pos = _POSITION_MAX_STARTERS.get(pos, 3)
                if position_counts.get(pos, 0) >= max_at_pos:
                    continue  # position saturated — skip
                selected.append(player)
                position_counts[pos] = position_counts.get(pos, 0) + 1
                if len(selected) >= requirements.starting_eleven_size:
                    break

    return selected[: requirements.starting_eleven_size]


def get_formation_string(players: list) -> str:
    """
    Derive formation string (e.g. '4-3-3') from a list of 11 players.
    Counts defenders, midfielders, and forwards (GK is always 1).
    """
    counts = get_position_counts(players)
    return f"{counts['Defender']}-{counts['Midfielder']}-{counts['Forward']}"


def order_for_lineup(players: list) -> list:
    """
    Order players by position for the set_lineup API: GK → DEF → MID → FWD.
    The API assigns players to formation slots by index, so the order must
    match the formation pattern.
    """
    position_order = {"Goalkeeper": 0, "Defender": 1, "Midfielder": 2, "Forward": 3}
    return sorted(players, key=lambda p: position_order.get(p.position, 9))


def can_fill_starting_eleven(available: list) -> dict[str, any]:
    """Can a legal starting 11 be built from these available players?

    Squad size alone does not answer this: a 13-man squad whose defenders are
    all injured still cannot field a legal formation. Called before a matchday
    so an emergency buy can be triggered while there is still time.

    Deliberately not ``validate_formation``: that function also gates on
    ``max_squad_size``, so passing it a 16-player *available* list returns
    ``can_field_eleven=False`` purely for being oversized, even though eleven
    are plainly fieldable. ``validate_formation`` answers "is this squad
    legal?" (including its total size); this function answers "can these
    available players field a legal eleven?" — a subset the caller has
    already filtered (e.g. excluding injured/suspended players), which has
    no upper bound to check.

    Args:
        available: players who can actually play (exclude injured/suspended)

    Returns:
        ``{"ok": bool, "reason": str, "counts": dict[str, int]}``
    """
    fb = fieldability(available)
    return {"ok": fb.ok, "reason": fb.reason, "counts": fb.counts}


def validate_trade(current_squad: list, players_out: list, players_in: list) -> dict[str, any]:
    """
    Validate an N-for-M trade

    Returns:
        dict with:
            - valid: bool
            - reason: str (if invalid)
            - squad_size_after: int
            - position_counts_after: dict
    """
    requirements = FormationRequirements()

    # Simulate squad after trade: remove players_out, add players_in
    player_out_ids = {p.id for p in players_out}
    squad_after_trade = [p for p in current_squad if p.id not in player_out_ids]
    squad_after_trade.extend(players_in)

    # Check max squad size
    if len(squad_after_trade) > requirements.max_squad_size:
        return {
            "valid": False,
            "reason": f"Would exceed max squad size: {len(squad_after_trade)}/{requirements.max_squad_size}",
            "squad_size_after": len(squad_after_trade),
            "position_counts_after": None,
        }

    # Validate formation
    validation = validate_formation(squad_after_trade, requirements)

    if not validation["valid"]:
        return {
            "valid": False,
            "reason": f"Would break formation: {', '.join(validation['issues'])}",
            "squad_size_after": validation["total_players"],
            "position_counts_after": validation["position_counts"],
        }

    return {
        "valid": True,
        "reason": None,
        "squad_size_after": validation["total_players"],
        "position_counts_after": validation["position_counts"],
    }
