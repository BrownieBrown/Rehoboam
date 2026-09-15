"""Calibration metrics, the gate and the Telegram message — pure (PR E §2).

Rows in, numbers out. `spearman` comes from the backtest so the live report
and the offline harness cannot disagree about what a rank correlation is;
the two elevens in `top11_regret` come from `select_best_eleven` so they are
always lineups Kickbase would accept.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import Any

from rehoboam.backtest.metrics import spearman
from rehoboam.formation import is_legal_formation, select_best_eleven

GATE_REQUIRED_REPORTS = 3
GATE_REQUIRED_CLEAN_DAYS = 7


@dataclass(frozen=True)
class CalRow:
    player_id: str
    position: str
    actual: float
    predicted: float | None
    baseline: float
    live: float | None
    owned: bool
    in_best_11: bool
    live_status: int | None
    played: bool = True


@dataclass(frozen=True)
class CalibrationReport:
    n: int
    n_unpredicted: int
    n_stale_rows: int
    mae: float | None
    bias: float | None
    spearman: float | None
    baseline_spearman: float | None
    spearman_played: float | None
    top11_regret: float | None
    baseline_top11_regret: float | None
    squad_regret: float | None
    live_spearman: float | None
    live_n: int
    by_position: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    worst: list[dict[str, Any]] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


def _predicted(rows: list[CalRow]) -> list[CalRow]:
    return [r for r in rows if r.predicted is not None]


def _mae(rows: list[CalRow]) -> float | None:
    if not rows:
        return None
    return sum(abs(r.predicted - r.actual) for r in rows) / len(rows)


def _bias(rows: list[CalRow]) -> float | None:
    if not rows:
        return None
    return sum(r.predicted - r.actual for r in rows) / len(rows)


def _spearman(rows: list[CalRow], key) -> float | None:
    pairs = [(key(r), r.actual) for r in rows if key(r) is not None]
    if len(pairs) < 2:
        return None
    return spearman([p for p, _ in pairs], [a for _, a in pairs])


def _best_total(rows: list[CalRow], key) -> float | None:
    """Actual points of the best legal eleven chosen by `key` (None = no
    eleven)."""
    candidates = [r for r in rows if key(r) is not None]
    squad = [SimpleNamespace(id=r.player_id, position=r.position) for r in candidates]
    eleven = select_best_eleven(squad, {r.player_id: key(r) for r in candidates})
    if len(eleven) < 11 or not is_legal_formation(eleven):
        return None
    actual = {r.player_id: r.actual for r in candidates}
    return sum(actual[p.id] for p in eleven)


def _league_regret(rows: list[CalRow], key) -> float | None:
    best = _best_total(rows, lambda r: r.actual)
    chosen = _best_total(rows, key)
    if best is None or chosen is None:
        return None
    return best - chosen


def _squad_regret(rows: list[CalRow]) -> float | None:
    owned = [r for r in rows if r.owned]
    fielded = [r for r in owned if r.in_best_11]
    if len(fielded) < 11:
        return None
    best = _best_total(owned, lambda r: r.actual)
    if best is None:
        return None
    return best - sum(r.actual for r in fielded)


def _bucket(rows: list[CalRow]) -> dict[str, Any]:
    p = _predicted(rows)
    return {
        "n": len(p),
        "mae": _mae(p),
        "bias": _bias(p),
        "spearman": _spearman(p, lambda r: r.predicted),
    }


def build_report(rows: list[CalRow], *, n_stale_rows: int = 0) -> CalibrationReport:
    predicted = _predicted(rows)
    by_position: dict[str, dict[str, Any]] = {}
    for pos in sorted({r.position for r in rows}):
        by_position[pos] = _bucket([r for r in rows if r.position == pos])
    by_status: dict[str, dict[str, Any]] = {}
    for status in sorted({r.live_status for r in predicted}, key=lambda s: (s is None, s)):
        by_status[str(status)] = _bucket([r for r in predicted if r.live_status == status])
    worst = sorted(predicted, key=lambda r: abs(r.predicted - r.actual), reverse=True)[:3]
    live_rows = [r for r in rows if r.live is not None]
    return CalibrationReport(
        n=len(predicted),
        n_unpredicted=len(rows) - len(predicted),
        n_stale_rows=n_stale_rows,
        mae=_mae(predicted),
        bias=_bias(predicted),
        spearman=_spearman(predicted, lambda r: r.predicted),
        baseline_spearman=_spearman(predicted, lambda r: r.baseline),
        spearman_played=_spearman([r for r in predicted if r.played], lambda r: r.predicted),
        top11_regret=_league_regret(predicted, lambda r: r.predicted),
        baseline_top11_regret=_league_regret(predicted, lambda r: r.baseline),
        squad_regret=_squad_regret(rows),
        live_spearman=_spearman(live_rows, lambda r: r.live),
        live_n=len(live_rows),
        by_position=by_position,
        by_status=by_status,
        worst=[
            {
                "player_id": r.player_id,
                "predicted": float(r.predicted),
                "actual": float(r.actual),
                "position": r.position,
            }
            for r in worst
        ],
    )


def _report_ok(r: CalibrationReport) -> tuple[bool | None, bool | None]:
    spearman_ok = (
        None
        if r.spearman is None or r.baseline_spearman is None
        else r.spearman >= r.baseline_spearman
    )
    regret_ok = (
        None
        if r.top11_regret is None or r.baseline_top11_regret is None
        else r.top11_regret < r.baseline_top11_regret
    )
    return spearman_ok, regret_ok


def gate_verdict(
    reports: list[CalibrationReport], *, last_failure_at: float | None, now: float
) -> dict[str, Any]:
    """The verdict the parent spec §4 describes, from real reports oldest
    first.

    `spearman_ok`/`regret_ok` describe the newest report. `consecutive_ok`
    is the trailing run where both held. No integrity failure on record
    counts as clean for the full window: the three-report requirement
    already guarantees weeks of sessions behind a pass.
    """
    consecutive = 0
    for r in reversed(reports):
        s, g = _report_ok(r)
        if s and g:
            consecutive += 1
        else:
            break
    newest = reports[-1] if reports else None
    spearman_ok, regret_ok = _report_ok(newest) if newest else (None, None)
    clean_days = (
        float(GATE_REQUIRED_CLEAN_DAYS)
        if last_failure_at is None
        else max(0.0, (now - last_failure_at) / 86400.0)
    )
    return {
        "spearman_ok": spearman_ok,
        "regret_ok": regret_ok,
        "consecutive_ok": consecutive,
        "required": GATE_REQUIRED_REPORTS,
        "integrity_clean_days": round(clean_days, 2),
        "required_clean_days": GATE_REQUIRED_CLEAN_DAYS,
        "passes": consecutive >= GATE_REQUIRED_REPORTS and clean_days >= GATE_REQUIRED_CLEAN_DAYS,
    }


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_calibration_message(
    report: CalibrationReport,
    *,
    season: str,
    day_number: int,
    names: dict[str, str],
    gate: dict[str, Any],
) -> str:
    worst = " · ".join(
        f"{names.get(w['player_id'], w['player_id'])} pred "
        f"{w['predicted']:.0f} act {w['actual']:.0f}"
        for w in report.worst
    )
    verdict = "open" if gate.get("passes") else "closed"
    lines = [
        f"Rehoboam calibration MD{day_number} {season}",
        f"n={report.n} (unpredicted {report.n_unpredicted})  mae "
        f"{_fmt(report.mae, 1)}  bias {_fmt(report.bias, 1)}",
        f"spearman {_fmt(report.spearman)} (baseline "
        f"{_fmt(report.baseline_spearman)}, played-only "
        f"{_fmt(report.spearman_played)})  top11 regret "
        f"{_fmt(report.top11_regret, 0)} (baseline "
        f"{_fmt(report.baseline_top11_regret, 0)})",
        f"squad regret {_fmt(report.squad_regret, 0)}  live-path spearman "
        f"{_fmt(report.live_spearman)} (n {report.live_n})",
        f"worst: {worst or 'none'}",
        f"gate: {gate.get('consecutive_ok', 0)}/"
        f"{gate.get('required', GATE_REQUIRED_REPORTS)} matchdays ok, "
        f"integrity clean {gate.get('integrity_clean_days', 0)}/"
        f"{gate.get('required_clean_days', GATE_REQUIRED_CLEAN_DAYS)} d — "
        f"{verdict}",
    ]
    return "\n".join(lines)
