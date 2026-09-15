"""Calibration metrics, the gate and the message are pure (PR E §2, Task 5)."""

from __future__ import annotations

import pytest

from rehoboam.services.calibration import (
    GATE_REQUIRED_CLEAN_DAYS,
    CalibrationReport,
    CalRow,
    build_report,
    gate_verdict,
    render_calibration_message,
)


def _row(
    pid,
    actual,
    predicted,
    *,
    position="Midfielder",
    baseline=None,
    live=None,
    owned=False,
    in_best_11=False,
    live_status=0,
):
    return CalRow(
        player_id=pid,
        position=position,
        actual=float(actual),
        predicted=predicted,
        baseline=float(baseline if baseline is not None else actual),
        live=live,
        owned=owned,
        in_best_11=in_best_11,
        live_status=live_status,
    )


def _league():
    """Eleven-plus per position so a legal eleven exists by every key."""
    rows = []
    pid = 0
    for pos, n in (
        ("Goalkeeper", 3),
        ("Defender", 8),
        ("Midfielder", 8),
        ("Forward", 6),
    ):
        for i in range(n):
            pid += 1
            actual = 100 - pid * 3
            rows.append(
                _row(
                    str(pid),
                    actual,
                    actual + (5 if i % 2 else -5),
                    position=pos,
                    baseline=actual - 10,
                )
            )
    return rows


class TestBuildReport:
    def test_counts_and_errors(self):
        rows = [_row("a", 10, 14), _row("b", 20, 18), _row("c", 30, None)]
        r = build_report(rows)
        assert (r.n, r.n_unpredicted) == (2, 1)
        assert r.mae == pytest.approx(3.0) and r.bias == pytest.approx(1.0)

    def test_perfect_ranking_is_spearman_one_and_zero_regret(self):
        rows = _league()
        for i, row in enumerate(rows):
            rows[i] = _row(
                row.player_id,
                row.actual,
                row.actual,
                position=row.position,
                baseline=row.baseline,
            )
        r = build_report(rows)
        assert r.spearman == pytest.approx(1.0) and r.top11_regret == pytest.approx(0.0)
        assert r.baseline_spearman == pytest.approx(1.0)  # a constant shift keeps the order

    def test_regret_is_best_eleven_by_actual_minus_best_eleven_by_prediction(self):
        rows = _league()
        # Push the true best forward to the bottom of the predicted order.
        top_fw = max((r for r in rows if r.position == "Forward"), key=lambda r: r.actual)
        rows = [
            (
                _row(
                    r.player_id,
                    r.actual,
                    -1.0,
                    position=r.position,
                    baseline=r.baseline,
                )
                if r is top_fw
                else r
            )
            for r in rows
        ]
        r = build_report(rows)
        assert r.top11_regret > 0

    def test_by_position_and_status_buckets(self):
        rows = [
            _row("a", 10, 12, position="Forward", live_status=0),
            _row("b", 20, 18, position="Forward", live_status=0),
            _row("c", 5, 30, position="Goalkeeper", live_status=4),
            _row("d", 0, None, position="Goalkeeper", live_status=None),
        ]
        r = build_report(rows)
        assert r.by_position["Forward"]["n"] == 2 and r.by_position["Forward"]["mae"] == 2.0
        assert r.by_position["Goalkeeper"]["n"] == 1
        assert set(r.by_status) == {"0", "4"} and r.by_status["4"]["bias"] == 25.0

    def test_worst_three_by_absolute_miss(self):
        rows = [_row(str(i), 0, float(i)) for i in range(1, 6)] + [_row("u", 0, None)]
        r = build_report(rows)
        assert [w["player_id"] for w in r.worst] == ["5", "4", "3"]
        assert r.worst[0] == {
            "player_id": "5",
            "predicted": 5.0,
            "actual": 0.0,
            "position": "Midfielder",
        }

    def test_squad_regret_is_the_points_left_on_the_bench(self):
        rows = _league()
        assert build_report(rows).squad_regret is None
        by_pos = {}
        for r in rows:
            by_pos.setdefault(r.position, []).append(r)
        # Twelve owned: a legal 4-4-2 eleven plus one benched defender.
        owned = (
            by_pos["Goalkeeper"][:1]
            + by_pos["Defender"][:5]
            + by_pos["Midfielder"][:4]
            + by_pos["Forward"][:2]
        )
        benched = by_pos["Defender"][0]
        fielded_ids = {r.player_id for r in owned if r is not benched}
        owned_ids = {r.player_id for r in owned}

        # Every owned player scored 50 except the benched defender, who scored 62:
        # whatever formation the hindsight eleven takes, fielding him is worth +12.
        def _owned_row(r):
            return _row(
                r.player_id,
                62.0 if r is benched else 50.0,
                r.predicted,
                position=r.position,
                baseline=r.baseline,
                owned=True,
                in_best_11=r.player_id in fielded_ids,
            )

        rows = [_owned_row(r) if r.player_id in owned_ids else r for r in rows]
        assert build_report(rows).squad_regret == pytest.approx(12.0)

    def test_live_spearman_over_rows_with_a_live_ep(self):
        rows = [
            _row("a", 10, 12, live=11.0),
            _row("b", 20, 18, live=19.0),
            _row("c", 30, 33),
        ]
        r = build_report(rows)
        assert r.live_n == 2 and r.live_spearman == pytest.approx(1.0)

    def test_empty_rows(self):
        r = build_report([])
        assert r.n == 0 and r.mae is None and r.spearman is None and r.worst == []


def _report(**over) -> CalibrationReport:
    base = build_report(_league())
    return CalibrationReport(**{**base.__dict__, **over})


class TestGate:
    NOW = 1_800_000_000.0

    def test_three_consecutive_wins_and_a_clean_week_pass(self):
        reports = [
            _report(
                spearman=0.5,
                baseline_spearman=0.4,
                top11_regret=100.0,
                baseline_top11_regret=150.0,
            )
        ] * 3
        g = gate_verdict(reports, last_failure_at=self.NOW - 8 * 86400, now=self.NOW)
        assert g["consecutive_ok"] == 3 and g["passes"] is True
        assert g["integrity_clean_days"] == pytest.approx(8.0)

    def test_a_loss_in_the_middle_resets_the_run(self):
        win = _report(
            spearman=0.5,
            baseline_spearman=0.4,
            top11_regret=100.0,
            baseline_top11_regret=150.0,
        )
        loss = _report(
            spearman=0.3,
            baseline_spearman=0.4,
            top11_regret=100.0,
            baseline_top11_regret=150.0,
        )
        g = gate_verdict([win, win, loss, win], last_failure_at=None, now=self.NOW)
        assert g["consecutive_ok"] == 1 and g["passes"] is False

    def test_regret_must_be_strictly_better(self):
        tie = _report(
            spearman=0.5,
            baseline_spearman=0.5,
            top11_regret=100.0,
            baseline_top11_regret=100.0,
        )
        g = gate_verdict([tie] * 3, last_failure_at=None, now=self.NOW)
        assert g["spearman_ok"] is True and g["regret_ok"] is False and g["passes"] is False

    def test_a_recent_integrity_failure_blocks(self):
        win = _report(
            spearman=0.5,
            baseline_spearman=0.4,
            top11_regret=100.0,
            baseline_top11_regret=150.0,
        )
        g = gate_verdict([win] * 3, last_failure_at=self.NOW - 3600, now=self.NOW)
        assert g["passes"] is False and g["integrity_clean_days"] < 1

    def test_no_failure_ever_counts_as_clean(self):
        win = _report(
            spearman=0.5,
            baseline_spearman=0.4,
            top11_regret=100.0,
            baseline_top11_regret=150.0,
        )
        g = gate_verdict([win] * 3, last_failure_at=None, now=self.NOW)
        assert g["integrity_clean_days"] == GATE_REQUIRED_CLEAN_DAYS and g["passes"] is True

    def test_no_reports(self):
        g = gate_verdict([], last_failure_at=None, now=self.NOW)
        assert g["consecutive_ok"] == 0 and g["passes"] is False
        assert g["spearman_ok"] is None and g["regret_ok"] is None


class TestMessage:
    def test_headline_numbers_and_worst_misses(self):
        rows = _league()
        r = build_report(rows)
        gate = gate_verdict([r], last_failure_at=None, now=1_800_000_000.0)
        text = render_calibration_message(
            r, season="2026/2027", day_number=4, names={"1": "Neuer"}, gate=gate
        )
        assert text.startswith("Rehoboam calibration MD4 2026/2027")
        assert f"n={r.n}" in text and "spearman" in text and "baseline" in text
        assert "Neuer" in text or "1 " in text
        assert "gate:" in text and ("closed" in text or "open" in text)
