"""Readers over `transfer_premiums` / `transfer_outcomes` (migration 024).

What the league's buyers paid over the market value in force, and what those
buys were worth afterwards -- per season and price band, and per manager.
Read-only; `derive-ceilings` and `transfer-study` print what comes back.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg

from rehoboam.services.ceiling_derivation import OurBid, WinnerRow
from rehoboam.services.profit_curve import OutcomeRow


def winners_since(conn: psycopg.Connection, since_epoch: float) -> list[WinnerRow]:
    """Every priced buy in the league since `since_epoch`, ours excluded."""
    rows = conn.execute(
        """
        select mv_at_transfer, premium_pct
        from rehoboam.transfer_premiums
        where premium_pct is not null
          and not is_self
          and transferred_at >= to_timestamp(%s)
        """,
        (since_epoch,),
    ).fetchall()
    return [WinnerRow(int(r["mv_at_transfer"]), float(r["premium_pct"])) for r in rows]


def outcomes_since(conn: psycopg.Connection, since_epoch: float) -> list[OutcomeRow]:
    """Every priced buy in the league since `since_epoch` with its resale result, ours excluded."""
    rows = conn.execute(
        """
        select mv_at_transfer, premium_pct, price, realised_profit
        from rehoboam.transfer_outcomes
        where premium_pct is not null
          and not is_self
          and transferred_at >= to_timestamp(%s)
        """,
        (since_epoch,),
    ).fetchall()
    return [
        OutcomeRow(
            int(r["mv_at_transfer"]),
            float(r["premium_pct"]),
            (
                100.0 * float(r["realised_profit"]) / float(r["price"])
                if r["realised_profit"] is not None and r["price"]
                else None
            ),
        )
        for r in rows
    ]


def our_bids_since(conn: psycopg.Connection, since_epoch: float) -> list[OurBid]:
    """The auctions we entered since `since_epoch` (`auction_outcomes`)."""
    rows = conn.execute(
        """
        select coalesce(market_value, asking_price) as market_value, our_overbid_pct, won
        from rehoboam.auction_outcomes
        where timestamp >= %s
        """,
        (since_epoch,),
    ).fetchall()
    return [
        OurBid(int(r["market_value"] or 0), float(r["our_overbid_pct"] or 0.0), bool(r["won"]))
        for r in rows
        if (r["market_value"] or 0) > 0
    ]


def study_by_band(
    conn: psycopg.Connection, *, bands: Sequence[int], season: str | None = None
) -> list[dict[str, Any]]:
    """Per season and price band: what was paid and what it was worth.

    A buy below the first band is dropped. Buys without a priced market value
    still count for points and resale, under band -1 ("unpriced").
    """
    rows = conn.execute(
        """
        select season,
               coalesce((select max(b) from unnest(%s::bigint[]) as b
                         where mv_at_transfer >= b), -1)                       as band,
               count(*)                                                        as buys,
               count(premium_pct)                                              as priced,
               percentile_cont(0.5) within group (order by premium_pct)        as premium_p50,
               percentile_cont(0.75) within group (order by premium_pct)       as premium_p75,
               percentile_cont(0.5) within group (order by avg_points_after)   as points_p50,
               count(sold_at)                                                  as resold,
               count(*) filter (where realised_profit > 0)                     as resold_at_profit,
               percentile_cont(0.5) within group (order by realised_profit)    as profit_p50,
               percentile_cont(0.5) within group (order by days_held)          as held_p50
        from rehoboam.transfer_outcomes
        where (%s::text is null or season = %s)
        group by 1, 2
        order by 1, 2
        """,
        ([int(b) for b in bands], season, season),
    ).fetchall()
    return [dict(r) for r in rows]


def study_by_manager(
    conn: psycopg.Connection, *, season: str | None = None
) -> list[dict[str, Any]]:
    """Per manager: how he buys and what it earns him."""
    rows = conn.execute(
        """
        select manager_id,
               coalesce(manager_name, manager_id)                              as manager,
               bool_or(is_self)                                                as is_self,
               count(*)                                                        as buys,
               percentile_cont(0.5) within group (order by premium_pct)        as premium_p50,
               percentile_cont(0.5) within group (order by avg_points_after)   as points_p50,
               sum(price)                                                      as spent,
               count(sold_at)                                                  as resold,
               count(*) filter (where realised_profit > 0)                     as resold_at_profit,
               sum(realised_profit)                                            as realised_profit
        from rehoboam.transfer_outcomes
        where (%s::text is null or season = %s)
        group by 1, 2
        order by realised_profit desc nulls last, buys desc
        """,
        (season, season),
    ).fetchall()
    return [dict(r) for r in rows]
