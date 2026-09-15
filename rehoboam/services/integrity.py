"""The integrity check (spec §3): pure, exhaustive, never blocks a lineup.

Each rule looks at facts the session already collected and says what is
wrong in words a Telegram reader can act on. A fact the session never
learned (None) skips the rule that needs it — I5 is the exception, because
"no predictions" is exactly what a missing count means.
"""

from __future__ import annotations

from rehoboam.services.session_facts import IntegrityFailure, SessionFacts

KICKOFF_LINEUP_WINDOW_S = 48 * 3600
INGEST_MAX_AGE_S = 36 * 3600


def i3_budget_covered(
    *, budget: int, open_offers_total: int, sellable_value: int
) -> tuple[bool, str]:
    """Budget minus open offers must be non-negative, or the deficit covered by
    what the squad could be sold for before kickoff."""
    net = int(budget) - int(open_offers_total)
    if net >= 0:
        return (
            True,
            f"budget EUR {budget:,} minus open offers EUR {open_offers_total:,} = EUR {net:,}",
        )
    deficit = -net
    if deficit <= int(sellable_value):
        return (
            True,
            f"deficit EUR {deficit:,} covered by sellable value EUR {sellable_value:,}",
        )
    return False, (
        f"budget EUR {budget:,} minus open offers EUR {open_offers_total:,} leaves a deficit of "
        f"EUR {deficit:,} that sellable value EUR {sellable_value:,} does not cover"
    )


def check_integrity(
    facts: SessionFacts, *, now: float, last_ingest_completed_at: float | None
) -> list[IntegrityFailure]:
    out: list[IntegrityFailure] = []

    if facts.next_kickoff is None:
        out.append(
            IntegrityFailure("I1", "next kickoff unknown (schedule and /myeleven both empty)")
        )

    if facts.fieldable_count is not None and facts.fieldable_count < 11:
        out.append(
            IntegrityFailure(
                "I2", f"only {facts.fieldable_count} fieldable after the emergency step"
            )
        )
    elif facts.fieldable_count is not None and facts.legal_formation is None:
        out.append(IntegrityFailure("I2", "no legal formation for the fieldable eleven"))

    if facts.budget is not None and facts.open_offers_total is not None:
        ok, detail = i3_budget_covered(
            budget=facts.budget,
            open_offers_total=facts.open_offers_total,
            sellable_value=facts.sellable_value or 0,
        )
        if not ok:
            out.append(IntegrityFailure("I3", detail))

    if facts.cost_basis_missing:
        out.append(
            IntegrityFailure(
                "I4", f"{facts.cost_basis_missing} owned player(s) without a cost basis"
            )
        )

    if not facts.predictions_written:
        out.append(IntegrityFailure("I5", "no predictions written this session"))

    if (
        facts.next_kickoff is not None
        and facts.next_kickoff - now <= KICKOFF_LINEUP_WINDOW_S
        and facts.lineup_result not in ("set", "dry_run")
    ):
        out.append(
            IntegrityFailure(
                "I6",
                f"lineup {facts.lineup_result or 'not attempted'} with kickoff within 48 h",
            )
        )

    if last_ingest_completed_at is None or now - last_ingest_completed_at > INGEST_MAX_AGE_S:
        age = (
            "never"
            if last_ingest_completed_at is None
            else f"{(now - last_ingest_completed_at) / 3600:.0f} h ago"
        )
        out.append(IntegrityFailure("I7", f"ingestion last completed {age} (limit 36 h)"))

    return out
