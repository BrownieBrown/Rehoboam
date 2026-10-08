"""Outside names → Kickbase players, within one club, only when unambiguous."""

from rehoboam.enrichment.names import by_team, match_player, normalize

UNIVERSE = [
    {"player_id": "8329", "first_name": "", "last_name": "Olise", "team_id": "2"},
    {"player_id": "7226", "first_name": "Harry", "last_name": "Kane", "team_id": "2"},
    {"player_id": "1", "first_name": "Luis", "last_name": "Díaz", "team_id": "2"},
    {"player_id": "2", "first_name": "Thomas", "last_name": "Müller", "team_id": "2"},
    {"player_id": "3", "first_name": "Nico", "last_name": "Müller", "team_id": "2"},
    {"player_id": "4", "first_name": "", "last_name": "Kane", "team_id": "9"},
    {"player_id": "5", "first_name": "Kaishu", "last_name": "Sano", "team_id": "18"},
    {"player_id": "6", "first_name": "Timo", "last_name": "Becker", "team_id": "8"},
    {"player_id": "7", "first_name": "Vitalie", "last_name": "Becker", "team_id": "8"},
    {"player_id": "8", "first_name": "", "last_name": "Fábio Silva", "team_id": "3"},
    {"player_id": "9", "first_name": "", "last_name": "Sambi Lokonga", "team_id": "6"},
    {"player_id": "10", "first_name": "", "last_name": "Kim", "team_id": "2"},
]


def test_normalize_strips_accents_case_and_hyphens():
    assert normalize("Luis Díaz") == "luis diaz"
    assert normalize("Stanišić") == "stanisic"
    assert normalize("Jean-Paul  Boëtius") == "jean paul boetius"
    assert normalize("Grønbæk") == "gronbaek"
    assert normalize(None) == ""


def test_last_name_alone_matches_the_one_player_on_the_club():
    teams = by_team(UNIVERSE)
    assert match_player("Olise", "2", teams) == "8329"
    assert match_player("Michael Olise", "2", teams) == "8329"


def test_the_club_disambiguates_a_shared_surname():
    teams = by_team(UNIVERSE)
    assert match_player("Harry Kane", "2", teams) == "7226"
    assert match_player("Kane", "9", teams) == "4"


def test_full_name_breaks_a_tie_on_the_same_club_and_a_bare_surname_does_not():
    teams = by_team(UNIVERSE)
    assert match_player("Thomas Müller", "2", teams) == "2"
    assert match_player("Müller", "2", teams) is None


def test_accents_on_either_side_are_ignored():
    teams = by_team(UNIVERSE)
    assert match_player("Luis Diaz", "2", teams) == "1"


def test_unknown_club_or_name_is_none():
    teams = by_team(UNIVERSE)
    assert match_player("Olise", None, teams) is None
    assert match_player("Olise", "99", teams) is None
    assert match_player("", "2", teams) is None


def test_a_first_name_alone_and_an_initial_resolve_the_way_ligainsider_shortens_names():
    teams = by_team(UNIVERSE)
    assert match_player("Kaishu", "18", teams) == "5"
    assert match_player("T. Becker", "8", teams) == "6"
    assert match_player("V. Becker", "8", teams) == "7"
    assert match_player("Becker", "8", teams) is None


def test_a_shared_token_matches_kickbase_display_names_without_a_first_name():
    teams = by_team(UNIVERSE)
    assert match_player("Silva", "3", teams) == "8"
    assert match_player("Sambi", "6", teams) == "9"
    assert match_player("Kim Min-Jae", "2", teams) == "10"
    assert match_player("Jobe", "3", teams) is None
