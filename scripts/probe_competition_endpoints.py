#!/usr/bin/env python3
"""Probe the competition-level endpoints for the v2 training corpus.

Validates, read-only:
  1. /v4/competitions/1/players                          -> full player universe?
  2. /v4/competitions/1/players/{pid}/performance        -> per-match history?
  3. /v4/competitions/1/players/{pid}/marketValue/365    -> MV series (already used)
  4. /v4/competitions/1/table                            -> Bundesliga standings?
  5. Candidate full-universe sources (endpoint 1 caps out at 25 players):
     5a. /v4/competitions/1/teams/{tid}/teamcenter        -> per-team roster?
     5b. /v4/leagues/{lid}/lineup/selection?start&max     -> does start/max paginate?

Answers the questions Tasks 3-5 depend on:
  - How many players does the universe endpoint return? Is it paginated?
  - Does competition-level performance match the league-level shape ({it:[{ph:[...]}]})?
  - Does the table endpoint expose a usable team-strength ordering?
  - Is there a real way to enumerate the full ~500-player Bundesliga universe?

Usage: uv run python scripts/probe_competition_endpoints.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

from rehoboam.kickbase_client import KickbaseV4Client  # noqa: E402

OUT_DIR = Path("/tmp/rehoboam_probe")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def dump(name: str, data) -> None:
    (OUT_DIR / f"{name}.json").write_text(json.dumps(data, indent=2)[:200_000])
    if isinstance(data, dict):
        print(f"  {name}: dict keys={list(data.keys())}")
    elif isinstance(data, list):
        print(f"  {name}: list len={len(data)}")


def main() -> int:
    client = KickbaseV4Client()
    if not client.login(os.environ["KICKBASE_EMAIL"], os.environ["KICKBASE_PASSWORD"]):
        print("LOGIN FAILED")
        return 1

    print("\n1. Player universe: /v4/competitions/1/players")
    universe = client.get_competition_players(competition_id="1")
    dump("competition_players", universe)
    items = universe.get("it") or universe.get("players") or []
    print(f"   -> {len(items)} players returned")
    if items:
        print(f"   -> sample player keys: {list(items[0].keys())}")
        print(f"   -> sample: {json.dumps(items[0])[:300]}")

    if not items:
        print("   !! empty universe — record this; Task 5 needs a fallback source")
        return 1

    pid_value = items[0].get("pi") or items[0].get("i") or items[0].get("id")
    if pid_value is None:
        print("   !! Could not find player ID field (tried 'pi', 'i', 'id')")
        return 1
    pid = str(pid_value)
    print(f"\n2. Performance for player {pid}")
    url = f"{client.BASE_URL}/v4/competitions/1/players/{pid}/performance"
    resp = client.session.get(url)
    print(f"   -> HTTP {resp.status_code}")
    if resp.status_code == 200:
        perf = resp.json()
        dump("competition_performance", perf)
        seasons = perf.get("it", [])
        print(f"   -> {len(seasons)} seasons; titles={[s.get('ti') for s in seasons][:5]}")
        if seasons and seasons[0].get("ph"):
            print(f"   -> sample match: {json.dumps(seasons[0]['ph'][0])[:300]}")

    print(f"\n3. MV history for player {pid}")
    mv = client.get_player_market_value_history_v2(player_id=pid, timeframe=365)
    dump("competition_marketvalue", mv)
    print(f"   -> {len(mv.get('it', []))} daily points")

    print("\n4. Table: /v4/competitions/1/table")
    resp = client.session.get(f"{client.BASE_URL}/v4/competitions/1/table")
    print(f"   -> HTTP {resp.status_code}")
    table_teams: list[dict] = []
    if resp.status_code == 200:
        table_data = resp.json()
        dump("competition_table", table_data)
        table_teams = table_data.get("it", [])

    print("\n5. Candidate full-universe sources (Task 5 needs ~500 players)")

    print("\n5a. Candidate 1: /v4/competitions/1/teams/{tid}/teamcenter")
    sample_team_ids = [t.get("tid") for t in table_teams[:2] if t.get("tid")]
    if not sample_team_ids:
        print("   !! no team ids available from step 4 table response")
    for tid in sample_team_ids:
        url = f"{client.BASE_URL}/v4/competitions/1/teams/{tid}/teamcenter"
        resp = client.session.get(url)
        print(f"   -> team {tid}: HTTP {resp.status_code}")
        if resp.status_code != 200:
            print(f"      {resp.text[:200]}")
            continue
        data = resp.json()
        dump(f"teamcenter_team{tid}", data)
        print(f"      top-level keys: {list(data.keys())}")
        players = data.get("it") or data.get("pl") or data.get("players") or data.get("squad") or []
        print(f"      -> {len(players)} players")
        if players:
            sample = players[0]
            print(f"      -> sample keys: {list(sample.keys())}")
            print(
                f"      -> has 'pi': {'pi' in sample}, "
                f"has 'mv': {'mv' in sample}, has 'ap': {'ap' in sample}"
            )
            print(f"      -> sample: {json.dumps(sample)[:300]}")

    print("\n5b. Candidate 2: /v4/leagues/{lid}/lineup/selection pagination")
    if not client.leagues:
        print("   !! no leagues on this account")
    else:
        league = client.leagues[0]
        league_id = league.id
        print(f"   using league {league.name!r} (id={league_id})")
        sel_url = f"{client.BASE_URL}/v4/leagues/{league_id}/lineup/selection"

        def ids_of(items: list[dict]) -> set[str]:
            out = set()
            for it in items:
                v = it.get("pi") or it.get("i") or it.get("id")
                if v is not None:
                    out.add(str(v))
            return out

        # First: literal params from the API reference, no position filter.
        resp = client.session.get(sel_url, params={"start": 0, "max": 100})
        no_pos_n = len((resp.json() or {}).get("it", [])) if resp.status_code == 200 else None
        print(
            f"   -> without 'position' filter: HTTP {resp.status_code}, {no_pos_n} items (need position to get data)"
        )

        # Discovered: the endpoint requires 'position' and its real page size is
        # 50 regardless of the requested 'max' — advance 'start' by the actual
        # returned count, not the requested max, or you silently skip players.
        grand_total: set[str] = set()
        per_position: dict[int, int] = {}
        for pos in (1, 2, 3, 4):
            pos_ids: set[str] = set()
            start = 0
            first_page_dumped = False
            for _ in range(20):  # safety cap: 20 * up-to-50 = 1000, well past ~500 expected
                resp = client.session.get(
                    sel_url, params={"position": pos, "start": start, "max": 50}
                )
                if resp.status_code != 200:
                    print(f"      position={pos} start={start}: HTTP {resp.status_code}")
                    break
                data = resp.json()
                if not first_page_dumped:
                    dump(f"lineup_selection_pos{pos}_start0", data)
                    first_page_dumped = True
                items = data.get("it", [])
                new_ids = ids_of(items) - pos_ids
                pos_ids |= new_ids
                if not items:
                    break
                start += len(items)
            per_position[pos] = len(pos_ids)
            grand_total |= pos_ids
            print(f"   -> position={pos}: {len(pos_ids)} distinct players")

        print(f"   -> per-position counts: {per_position}")
        print(
            f"   -> GRAND TOTAL distinct players via lineup/selection (positions 1-4): {len(grand_total)}"
        )

        # Field-name check on one non-empty sample.
        resp = client.session.get(sel_url, params={"position": 1, "start": 0, "max": 5})
        if resp.status_code == 200:
            sample_items = resp.json().get("it", [])
            if sample_items:
                sample = sample_items[0]
                print(f"   -> sample keys: {list(sample.keys())}")
                print(
                    f"   -> has 'pi': {'pi' in sample}, "
                    f"has 'mv': {'mv' in sample}, has 'ap': {'ap' in sample}"
                )
                print(f"   -> sample: {json.dumps(sample)[:300]}")

    print(f"\nDumps written to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
