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
    """
    Select the best starting 11 from squad based on value scores

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

    # Second pass: fill remaining spots with best available
    if len(selected) < requirements.starting_eleven_size:
        for player in sorted_squad:
            if player not in selected:
                selected.append(player)
                if len(selected) >= requirements.starting_eleven_size:
                    break

    return selected[: requirements.starting_eleven_size]


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
