#!/usr/bin/env python3
"""Re-verify `formation.LEGAL_FORMATIONS` against the live Kickbase API.

Read-only. `GET /v4/config` returns a `cps` list (one entry per competition),
each carrying `lts` — the lineup types Kickbase accepts, as "4-4-2" strings —
and `lpc`, how many players that competition fields. Only the `lpc == 11`
competitions are comparable: Kickbase also runs six-a-side modes whose `lts`
is 2-1-2 / 1-2-2 / 2-2-1, and folding those in would "find" three formations
the Bundesliga will never accept. That list is the authority for which elevens
can ever be submitted, and the bot keeps a copy in `rehoboam/formation.py`
because fieldability, the emergency basket and `select_best_eleven` all read
it offline.

A copy that has drifted costs points in both directions: a formation missing
from ours is an eleven we never field, and one we list that Kickbase rejects
is a lineup that is never set at all (`LineupNotEnoughPlayers`). So run this
whenever either happens, and at the start of a season.

Usage: uv run python scripts/probe_formations.py
Exit code: 0 when the code matches the API, 1 on any mismatch or failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Credentials come from `.env` the way the CLI reads them — through
# `Settings`, which strips the inline comments a hand-rolled parser trips on.
from rehoboam.cli import _login_and_get_league  # noqa: E402
from rehoboam.formation import LEGAL_FORMATIONS  # noqa: E402

#: `cps[].lpc` — players a competition fields. Ours is eleven-a-side.
ELEVEN_A_SIDE = 11


def _as_strings(formations) -> set[str]:
    return {f"{d}-{m}-{f}" for d, m, f in formations}


def main() -> int:
    # `_login_and_get_league` returns (api, settings, league) — the API wrapper
    # is the one carrying the HTTP client.
    parts = _login_and_get_league(0)
    api = next(p for p in parts if hasattr(p, "client"))
    client = api.client

    response = client.session.get(f"{client.BASE_URL}/v4/config")
    if response.status_code != 200:
        print(f"GET /v4/config failed: {response.status_code} - {response.text[:300]}")
        return 1
    config = response.json()

    competitions = config.get("cps") or []
    if not competitions:
        print(f"/v4/config carries no `cps`; keys={list(config)}")
        return 1

    from_api: set[str] = set()
    for comp in competitions:
        lts = [str(x) for x in (comp.get("lts") or [])]
        players = comp.get("lpc")
        eleven = players == ELEVEN_A_SIDE
        print(
            f"  competition cpi={comp.get('cpi')} lpc={players}"
            f"{'' if eleven else '  (skipped: not eleven-a-side)'}: "
            f"lts = {', '.join(lts)}"
        )
        if eleven:
            from_api.update(lts)

    if not from_api:
        print(f"no competition reported lpc={ELEVEN_A_SIDE}; nothing to compare")
        return 1

    in_code = _as_strings(LEGAL_FORMATIONS)
    print(f"\n  /v4/config   ({len(from_api)}): {', '.join(sorted(from_api))}")
    print(f"  formation.py ({len(in_code)}): {', '.join(sorted(in_code))}")

    if from_api == in_code:
        print("\n✓ LEGAL_FORMATIONS matches the API")
        return 0

    missing = sorted(from_api - in_code)
    extra = sorted(in_code - from_api)
    print("\n✗ MISMATCH — update rehoboam/formation.py")
    if missing:
        print(f"  in the API, not in the code (elevens we never field): {', '.join(missing)}")
    if extra:
        print(f"  in the code, not in the API (lineups Kickbase rejects): {', '.join(extra)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
