#!/usr/bin/env python3
"""Probe the competition-level endpoints for the v2 training corpus.

Validates, read-only:
  1. /v4/competitions/1/players                          -> full player universe?
  2. /v4/competitions/1/players/{pid}/performance        -> per-match history?
  3. /v4/competitions/1/players/{pid}/marketValue/365    -> MV series (already used)
  4. /v4/competitions/1/table                            -> Bundesliga standings?

Answers the questions Tasks 3-5 depend on:
  - How many players does the universe endpoint return? Is it paginated?
  - Does competition-level performance match the league-level shape ({it:[{ph:[...]}]})?
  - Does the table endpoint expose a usable team-strength ordering?

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
    if resp.status_code == 200:
        dump("competition_table", resp.json())

    print(f"\nDumps written to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
