"""M1 of the v3 spec: is 7-day drift predictable out of sample? (§4.2, §10)

The hurdle was written before the first run. Fit the drift table on value paths
before the cutoff, then predict every Kickbase listing after it. Among the top
decile by predicted drift, the median of ``(1 + realised) / (1 + band premium) - 1``
must be above zero. The realised drift runs over the 7 nights after the auction
settles; the band premium is the league's median premium for the price band in
the season. If it fails, the bot cannot trade for drift and the spec stops at
M1.

Also reported, never gating:

- a decile table (calibration);
- Spearman of predicted against realised;
- fixed-premium bidding on the top decile, auctions resolved by `sim.auction`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean, median

from rehoboam.backtest.metrics import spearman
from rehoboam.desk import drift
from rehoboam.sim import auction, history
from rehoboam.sim.history import Listing, Transfer, ValuePaths, value_day

HORIZON_DAYS = 7
LAG_DAYS = 1
DECILES = 10
FIXED_PREMIUMS = (0.0, 0.02, 0.05)


@dataclass(frozen=True)
class ListingDrift:
    player_id: str
    expires_at: datetime
    band: int
    predicted: float
    realised: float
    band_premium: float
    rival: auction.Rival
    lag: int  # settle day minus the day the features were read
    settle_value: int = 0  # market value at settle, euros

    @property
    def excess(self) -> float:
        return (1 + self.realised) / (1 + self.band_premium) - 1


@dataclass(frozen=True)
class DecileRow:
    decile: int  # 1 = highest predicted drift
    n: int
    mean_predicted: float
    median_realised: float
    mean_realised: float


@dataclass(frozen=True)
class BidRow:
    premium: float | None  # None = the band's median premium
    won: int
    unknown: int
    median_return: float  # (1 + realised) / (1 + premium) - 1 on the wins


@dataclass(frozen=True)
class BandRow:
    """The hurdle inside one price band: informational, the pooled verdict gates."""

    band: int
    listings: int
    top: int
    band_premium: float
    top_median_realised: float
    top_median_excess: float
    top_median_gain_eur: float  # market value at settle × realised drift


@dataclass(frozen=True)
class HurdleReport:
    fit_cutoff: date
    samples: int
    listings: int
    top: int
    spearman: float
    top_median_realised: float
    top_median_excess: float
    passed: bool
    deciles: tuple[DecileRow, ...]
    bids: tuple[BidRow, ...]
    lags: Mapping[int, int]
    bands: tuple[BandRow, ...] = ()


def band_premiums(premiums: Iterable[tuple[int, float]]) -> dict[int, float]:
    grouped: dict[int, list[float]] = {}
    for value, premium in premiums:
        grouped.setdefault(drift.band(value), []).append(premium)
    return {b: median(ps) for b, ps in grouped.items()}


def fit_table(
    paths: ValuePaths, moves: Mapping[date, float], cutoff: date
) -> tuple[drift.DriftTable, int]:
    first = min(day for path in paths.values() for day in path)
    last = cutoff - timedelta(days=LAG_DAYS + HORIZON_DAYS)
    samples = list(
        drift.training_samples(
            paths, moves, lag=LAG_DAYS, horizon=HORIZON_DAYS, first_day=first, last_day=last
        )
    )
    return drift.fit(samples, lag=LAG_DAYS, horizon=HORIZON_DAYS), len(samples)


def listing_drifts(
    listings: Iterable[Listing],
    paths: ValuePaths,
    moves: Mapping[date, float],
    table: drift.DriftTable,
    premium_by_band: Mapping[int, float],
    buys: Mapping[str, Sequence[Transfer]],
    self_ids: frozenset[str],
    *,
    after: date,
) -> list[ListingDrift]:
    out: list[ListingDrift] = []
    for listing in listings:
        path = paths.get(listing.player_id)
        seen, settle = value_day(listing.last_seen), value_day(listing.expires_at)
        if path is None or seen < after:
            continue
        features = drift.features_at(path, seen, drift.market_falling(moves, seen))
        realised = drift.target_at(path, settle, lag=0, horizon=HORIZON_DAYS)
        if features is None or realised is None:
            continue
        b = drift.band(features.market_value)
        if b not in premium_by_band:
            continue
        out.append(
            ListingDrift(
                player_id=listing.player_id,
                expires_at=listing.expires_at,
                band=b,
                predicted=table.predict(features).mean,
                realised=realised,
                band_premium=premium_by_band[b],
                rival=auction.rival_for(listing, buys, paths, self_ids),
                lag=(settle - seen).days,
                settle_value=path[settle],
            )
        )
    return out


def hurdle(drifts: Sequence[ListingDrift], *, fit_cutoff: date, samples: int) -> HurdleReport:
    if not drifts:
        raise ValueError("no listings to judge the hurdle on")
    ranked = sorted(drifts, key=lambda d: d.predicted, reverse=True)
    top = ranked[: math.ceil(len(ranked) / DECILES)]
    top_excess = median(d.excess for d in top)
    lags: dict[int, int] = {}
    for d in drifts:
        lags[d.lag] = lags.get(d.lag, 0) + 1
    return HurdleReport(
        fit_cutoff=fit_cutoff,
        samples=samples,
        listings=len(drifts),
        top=len(top),
        spearman=spearman([d.predicted for d in drifts], [d.realised for d in drifts]),
        top_median_realised=median(d.realised for d in top),
        top_median_excess=top_excess,
        passed=top_excess > 0,
        deciles=_deciles(ranked),
        bids=tuple(_bid_row(top, p) for p in (*FIXED_PREMIUMS, None)),
        lags=lags,
        bands=_band_rows(drifts),
    )


def _band_rows(drifts: Sequence[ListingDrift]) -> tuple[BandRow, ...]:
    rows = []
    for b in sorted({d.band for d in drifts}):
        ranked = sorted((d for d in drifts if d.band == b), key=lambda d: d.predicted, reverse=True)
        top = ranked[: math.ceil(len(ranked) / DECILES)]
        rows.append(
            BandRow(
                band=b,
                listings=len(ranked),
                top=len(top),
                band_premium=top[0].band_premium,
                top_median_realised=median(d.realised for d in top),
                top_median_excess=median(d.excess for d in top),
                top_median_gain_eur=median(d.settle_value * d.realised for d in top),
            )
        )
    return tuple(rows)


def _deciles(ranked: Sequence[ListingDrift]) -> tuple[DecileRow, ...]:
    size = math.ceil(len(ranked) / DECILES)
    rows = []
    for i in range(DECILES):
        chunk = ranked[i * size : (i + 1) * size]
        if not chunk:
            break
        rows.append(
            DecileRow(
                decile=i + 1,
                n=len(chunk),
                mean_predicted=mean(d.predicted for d in chunk),
                median_realised=median(d.realised for d in chunk),
                mean_realised=mean(d.realised for d in chunk),
            )
        )
    return tuple(rows)


def _bid_row(top: Sequence[ListingDrift], premium: float | None) -> BidRow:
    won: list[float] = []
    unknown = 0
    for d in top:
        p = d.band_premium if premium is None else premium
        result = auction.wins(p, d.rival)
        if result is None:
            unknown += 1
        elif result:
            won.append((1 + d.realised) / (1 + p) - 1)
    return BidRow(
        premium=premium,
        won=len(won),
        unknown=unknown,
        median_return=median(won) if won else math.nan,
    )


def run(conn, *, fit_cutoff: date, season: str, now: datetime) -> HurdleReport:
    """Load, fit before the cutoff, judge the listings after it. Read-only."""
    paths = history.load_value_paths(conn)
    moves = drift.market_moves(paths)
    table, samples = fit_table(paths, moves, fit_cutoff)
    listings = history.load_kickbase_listings(conn, expired_before=now)
    first_listed = min((listing.listed_at for listing in listings), default=now)
    buys = auction.buys_by_player(history.load_transfers(conn, since=first_listed))
    premium_by_band = band_premiums(history.load_league_premiums(conn, season=season))
    drifts = listing_drifts(
        listings,
        paths,
        moves,
        table,
        premium_by_band,
        buys,
        history.load_self_ids(conn),
        after=fit_cutoff,
    )
    return hurdle(drifts, fit_cutoff=fit_cutoff, samples=samples)


def format_report(r: HurdleReport) -> list[str]:
    verdict = "PASSED" if r.passed else "FAILED"
    lines = [
        f"Drift table fitted on value paths before {r.fit_cutoff} ({r.samples:,} samples).",
        f"Kickbase listings judged: {r.listings} (feature-to-settle lag in days: {dict(sorted(r.lags.items()))})",
        f"Spearman(predicted, realised 7d): {r.spearman:+.3f}",
        f"Top decile (n={r.top}): median realised {r.top_median_realised:+.2%}, "
        f"median excess over the band premium {r.top_median_excess:+.2%} -> hurdle {verdict}",
        "",
        "decile  n    mean predicted  median realised  mean realised",
    ]
    lines += [
        f"{row.decile:>6}  {row.n:<4} {row.mean_predicted:>+14.2%}  {row.median_realised:>+15.2%}  {row.mean_realised:>+13.2%}"
        for row in r.deciles
    ]
    lines += ["", "Per band (informational; the pooled verdict above gates):"]
    names = {0: "<5m", 1: "5-15m", 2: "15m+"}
    for band_row in r.bands:
        lines.append(
            f"  {names.get(band_row.band, band_row.band):>6}: {band_row.listings} listings, "
            f"top {band_row.top}: median realised {band_row.top_median_realised:+.2%}, "
            f"excess over {band_row.band_premium:+.1%} {band_row.top_median_excess:+.2%}, "
            f"median gain EUR {band_row.top_median_gain_eur:,.0f}"
        )
    lines += [
        "",
        "Top decile, bid at a fixed premium (auctions resolved against the league's buyers):",
    ]
    for row in r.bids:
        label = "band median" if row.premium is None else f"{row.premium:+.0%}"
        lines.append(
            f"  {label:>11}: won {row.won}, unknown {row.unknown}, median return on wins {row.median_return:+.2%}"
        )
    return lines
