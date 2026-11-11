"""M0 of the v3 spec: the simulator must reproduce history first (§8.3, B5).

Three checks on what the store can observe:

1. **Squads.** Replaying a manager's transfers from his first stored squad must
   reproduce every later stored squad.
2. **Values.** Each player's market value under the 22:00 Berlin rule
   (`history.value_day`) must reproduce the value Kickbase reported in the squad
   snapshot. Team values are sums, so they follow.
3. **Our wallet.** Between two budget readings, our transfers must explain the
   change. Only the daily bonus and the matchday points bonus may be left over,
   each well under `WALLET_RESIDUAL_LIMIT`. Rivals' cash is not visible, so for
   them this check cannot exist.

A simulator that can't reproduce what happened can't be trusted on what didn't.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from rehoboam.sim.history import (
    BUY,
    SELL,
    SquadSnapshot,
    Transfer,
    ValuePaths,
    WalletReading,
    value_day,
)

SQUAD_MATCH_MIN = 0.95  # share of snapshots whose squad must replay exactly
VALUE_TOLERANCE_PCT = 0.05
VALUE_TOLERANCE_EUR = 2_000_000
VALUE_MATCH_MIN = 0.95
WALLET_RESIDUAL_LIMIT = 1_000_000  # more than any bonus between two readings


def replay_holdings(
    start: frozenset[str], transfers: Iterable[Transfer], after: datetime, until: datetime
) -> frozenset[str]:
    """``start`` with every buy added and every sale removed in (after, until]."""
    held = set(start)
    for t in transfers:
        if after < t.at <= until:
            if t.kind == BUY:
                held.add(t.player_id)
            elif t.kind == SELL:
                held.discard(t.player_id)
    return frozenset(held)


@dataclass(frozen=True)
class ManagerCheck:
    manager_id: str
    snapshots: int
    squad_matches: int
    priced: int  # snapshots whose every player has a value that day
    value_matches: int
    median_value_error: float  # relative, simulated against reported team value
    first_mismatch: datetime | None

    @property
    def passed(self) -> bool:
        if self.snapshots == 0:
            return True
        squads_ok = self.squad_matches / self.snapshots >= SQUAD_MATCH_MIN
        values_ok = self.priced == 0 or self.value_matches / self.priced >= VALUE_MATCH_MIN
        return squads_ok and values_ok


@dataclass(frozen=True)
class WalletCheck:
    intervals: int
    unexplained: tuple[tuple[datetime, int], ...]  # (reading time, residual) over the limit
    median_residual: float

    @property
    def passed(self) -> bool:
        return not self.unexplained


def check_manager(
    snapshots: Sequence[SquadSnapshot], transfers: Sequence[Transfer], paths: ValuePaths
) -> ManagerCheck:
    ordered = sorted(snapshots, key=lambda s: s.at)
    start, later = ordered[0], ordered[1:]
    squad_ok = value_ok = priced = 0
    errors: list[float] = []
    first_mismatch: datetime | None = None
    for snap in later:
        rebuilt = replay_holdings(frozenset(start.players), transfers, start.at, snap.at)
        same_squad = rebuilt == frozenset(snap.players)
        squad_ok += same_squad
        if not same_squad and first_mismatch is None:
            first_mismatch = snap.at
        simulated = _team_value(snap.players, paths, snap.at)
        if simulated is None:  # a player has no value that day: uncovered, not wrong
            continue
        priced += 1
        reported = sum(snap.players.values())
        error = abs(simulated - reported)
        errors.append(error / reported if reported else 0.0)
        value_ok += error <= max(VALUE_TOLERANCE_PCT * reported, VALUE_TOLERANCE_EUR)
    return ManagerCheck(
        manager_id=start.manager_id,
        snapshots=len(later),
        squad_matches=squad_ok,
        priced=priced,
        value_matches=value_ok,
        median_value_error=median(errors) if errors else 0.0,
        first_mismatch=first_mismatch,
    )


def _team_value(players: Iterable[str], paths: ValuePaths, at: datetime) -> int | None:
    day = value_day(at)
    values = [paths.get(pid, {}).get(day) for pid in players]
    if any(v is None for v in values):
        return None
    return sum(v for v in values if v is not None)


def check_wallet(
    readings: Sequence[WalletReading], our_transfers: Sequence[Transfer]
) -> WalletCheck:
    """Budget changes our transfers do not explain, net of same-second races.

    A reading taken in the second a transfer lands can show the budget before
    the transfer while the transfer's timestamp says it happened. The residual
    then reverses at the next reading. Only a residual that does not reverse
    is unexplained.
    """
    residuals = [
        (after.at, (after.budget - before.budget) - _net(our_transfers, before.at, after.at))
        for before, after in zip(readings, readings[1:], strict=False)
    ]
    unexplained = [
        (at, residual)
        for i, (at, residual) in enumerate(residuals)
        if abs(residual) > WALLET_RESIDUAL_LIMIT
        and not _reversed(residual, residuals[i + 1][1] if i + 1 < len(residuals) else None)
        and not _reversed(residual, residuals[i - 1][1] if i > 0 else None)
    ]
    return WalletCheck(
        intervals=len(residuals),
        unexplained=tuple(unexplained),
        median_residual=median(r for _, r in residuals) if residuals else 0.0,
    )


def _net(transfers: Iterable[Transfer], after: datetime, until: datetime) -> int:
    return sum(
        (t.price if t.kind == SELL else -t.price) for t in transfers if after < t.at <= until
    )


def _reversed(residual: int, neighbour: int | None) -> bool:
    return neighbour is not None and abs(residual + neighbour) <= WALLET_RESIDUAL_LIMIT


@dataclass(frozen=True)
class Validation:
    managers: tuple[ManagerCheck, ...]
    wallet: WalletCheck

    @property
    def passed(self) -> bool:
        return all(m.passed for m in self.managers) and self.wallet.passed


def validate(
    snapshots: Sequence[SquadSnapshot],
    transfers: Sequence[Transfer],
    paths: ValuePaths,
    wallet: Sequence[WalletReading],
    self_ids: frozenset[str],
) -> Validation:
    by_manager: dict[str, list[SquadSnapshot]] = defaultdict(list)
    for snap in snapshots:
        by_manager[snap.manager_id].append(snap)
    transfers_by: Mapping[str, list[Transfer]] = _group(transfers)
    checks = tuple(
        check_manager(snaps, transfers_by.get(manager, []), paths)
        for manager, snaps in sorted(by_manager.items())
    )
    ours = [t for t in transfers if t.manager_id in self_ids]
    start = min((s.at for s in snapshots), default=None)
    readings = [r for r in wallet if start is None or r.at >= start]
    return Validation(managers=checks, wallet=check_wallet(readings, ours))


def _group(transfers: Iterable[Transfer]) -> dict[str, list[Transfer]]:
    grouped: dict[str, list[Transfer]] = defaultdict(list)
    for t in transfers:
        grouped[t.manager_id].append(t)
    return grouped


def format_validation(v: Validation, names: Mapping[str, str]) -> list[str]:
    lines = [
        "manager            snaps  squad ok  priced  value ok  median value error  first squad mismatch"
    ]
    for m in v.managers:
        lines.append(
            f"{names.get(m.manager_id, m.manager_id)[:18]:<18} {m.snapshots:>5}  "
            f"{m.squad_matches:>8}  {m.priced:>6}  {m.value_matches:>8}  {m.median_value_error:>18.2%}  "
            f"{m.first_mismatch or '-'}{'' if m.passed else '  FAIL'}"
        )
    w = v.wallet
    lines += [
        "",
        f"Our wallet: {w.intervals} intervals, median residual {w.median_residual:,.0f}, "
        f"{len(w.unexplained)} over {WALLET_RESIDUAL_LIMIT:,}",
    ]
    lines += [f"  {at}: {residual:+,}" for at, residual in w.unexplained[:10]]
    lines.append(f"B5 (simulator reproduces history): {'PASSED' if v.passed else 'FAILED'}")
    return lines
