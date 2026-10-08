"""ligainsider club page → predicted eleven; the refresh step's kickoff gate."""

from __future__ import annotations

from pathlib import Path

from rehoboam.enrichment import ligainsider
from rehoboam.enrichment.ligainsider import (
    ALTERNATIVE_SLOT_BASE,
    Named,
    ParsedLineup,
    lineup_rows,
    parse_team_page,
    run_predicted_xi_refresh,
    slug_name,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ligainsider_fcb_2026_10_05.html"
PAGE = FIXTURE.read_text(encoding="utf-8")

UNIVERSE = [
    {"player_id": "100", "first_name": "Manuel", "last_name": "Neuer", "team_id": "2"},
    {"player_id": "101", "first_name": "", "last_name": "Laimer", "team_id": "2"},
    {"player_id": "102", "first_name": "Josip", "last_name": "Stanišić", "team_id": "2"},
    {"player_id": "7226", "first_name": "Harry", "last_name": "Kane", "team_id": "2"},
    {"player_id": "8329", "first_name": "", "last_name": "Olise", "team_id": "2"},
    {"player_id": "103", "first_name": "Luis", "last_name": "Díaz", "team_id": "2"},
    {"player_id": "104", "first_name": "Jamal", "last_name": "Musiala", "team_id": "2"},
    {"player_id": "105", "first_name": "", "last_name": "Upamecano", "team_id": "2"},
]


def test_parses_eleven_starters_in_pitch_order_and_the_alternatives_beside_them():
    parsed = parse_team_page(PAGE)
    assert parsed.complete
    assert [n.name for n in parsed.starters] == [
        "Neuer",
        "Laimer",
        "Upamecano",
        "Tah",
        "Davies",
        "Kimmich",
        "Saibari",
        "Pavlović",
        "Olise",
        "Kane",
        "Luis Díaz",
    ]
    assert [n.name for n in parsed.alternatives] == ["Stanišić", "Brown", "Karl"]
    assert parsed.starters[0].slug == "manuel-neuer_114"


def test_reads_the_opponent_the_update_stamp_and_the_missing_players():
    parsed = parse_team_page(PAGE)
    assert parsed.opponent == "FC Augsburg"
    assert parsed.updated_at is not None and "05.10.2026" in parsed.updated_at
    assert [n.name for n in parsed.missing] == ["Jamal Musiala"]


def test_a_page_without_the_section_is_incomplete_and_empty():
    parsed = parse_team_page("<html><body>nothing here</body></html>")
    assert not parsed.complete
    assert parsed.starters == () and parsed.missing == ()


def test_rows_mark_the_eleven_the_alternatives_and_the_missing():
    parsed = parse_team_page(PAGE)
    rows, unmatched = lineup_rows(
        "2", parsed, season="2026/2027", day_number=5, fetched_at=1.0, universe=UNIVERSE
    )
    by_name = {r["player_name"]: r for r in rows}
    assert by_name["Neuer"]["in_xi"] is True and by_name["Neuer"]["slot"] == 1
    assert by_name["Neuer"]["player_id"] == "100"
    assert by_name["Luis Díaz"]["player_id"] == "103" and by_name["Luis Díaz"]["slot"] == 11
    assert by_name["Stanišić"]["in_xi"] is False
    assert by_name["Stanišić"]["slot"] == ALTERNATIVE_SLOT_BASE + 1
    assert by_name["Jamal Musiala"]["in_xi"] is False and by_name["Jamal Musiala"]["slot"] is None
    assert by_name["Jamal Musiala"]["player_id"] == "104"
    assert all(r["source"] == "ligainsider" and r["day_number"] == 5 for r in rows)
    # names the universe does not carry stay as rows without a player id
    assert "Tah (2)" in unmatched and by_name["Tah"]["player_id"] is None
    # a first-name-only display ("Kaishu") resolves through the slug's full name
    assert by_name["Upamecano"]["player_id"] == "105"


class _Store:
    def __init__(self, md):
        self.md = md
        self.written = []

    def upcoming_matchday(self, now):
        return self.md

    def universe(self):
        return UNIVERSE

    def write(self, rows):
        self.written = rows
        return len(rows)


DAY = 86400.0
MD = {"season": "2026/2027", "day_number": 5, "first_kickoff": 10 * DAY, "last_kickoff": 12 * DAY}


def test_refresh_waits_until_kickoff_is_within_the_window(monkeypatch):
    store = _Store(MD)
    monkeypatch.setattr(ligainsider, "fetch_team_page", lambda *a, **k: 1 / 0)
    out = run_predicted_xi_refresh(store, now=5 * DAY, days_before=2.0, sleep=lambda s: None)
    assert out["matchday"] == 5
    assert out["skipped"].startswith("kickoff in 5.0 d")
    assert out["written"] == 0


def test_refresh_without_fixtures_is_a_skip_not_an_error():
    out = run_predicted_xi_refresh(_Store(None), now=1.0, days_before=2.0, sleep=lambda s: None)
    assert out["skipped"] == "no upcoming matchday in fixtures"
    assert out["error"] is None


def test_refresh_fetches_every_club_inside_the_window_and_survives_one_failure(monkeypatch):
    store = _Store(MD)
    calls = []

    def fake_fetch(path, **kw):
        calls.append(path)
        if "fail" in path:
            raise RuntimeError("503")
        if "short" in path:
            return '<h1>VORAUSSICHTLICHE AUFSTELLUNG</h1><div class="player_position_row"></div>'
        return PAGE

    monkeypatch.setattr(ligainsider, "fetch_team_page", fake_fetch)
    slept = []
    out = run_predicted_xi_refresh(
        store,
        now=9 * DAY,
        days_before=2.0,
        throttle_seconds=1.5,
        team_paths={"2": "/fc-bayern-muenchen/1/", "3": "/fail/", "4": "/short/"},
        sleep=slept.append,
    )
    assert calls == ["/fc-bayern-muenchen/1/", "/fail/", "/short/"]
    assert out["teams"] == 1
    assert out["failed"] == ["3"] and out["incomplete"] == ["4"]
    assert out["written"] == 15  # 11 starters + 3 alternatives + 1 missing
    assert slept == [1.5, 1.5, 1.5]
    assert store.written[0]["season"] == "2026/2027" and store.written[0]["day_number"] == 5


def test_the_slug_resolves_a_display_name_the_universe_cannot():
    assert slug_name("kaishu-sano_12345") == "kaishu sano"
    lineup = ParsedLineup(
        starters=(Named("Kaishu", "kaishu-sano_1"),) * 11,
        alternatives=(),
        missing=(),
        opponent=None,
        updated_at=None,
    )
    universe = [{"player_id": "77", "first_name": "", "last_name": "Sano", "team_id": "18"}]
    rows, unmatched = lineup_rows(
        "18", lineup, season="2026/2027", day_number=5, fetched_at=1.0, universe=universe
    )
    assert rows[0]["player_id"] == "77" and unmatched == []
