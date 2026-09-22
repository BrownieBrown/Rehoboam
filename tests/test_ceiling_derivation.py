"""`derive-ceilings`: propose price-band overbid caps from the auction ledger.

Pure. The command reads `auction_outcomes`, hands the rows here, prints what
comes back and changes nothing. A band's proposal is the 75th percentile of
what winners paid over market value in that band — a bid there beats three of
four winning bids — and the whole proposal is withheld below `min_winners`
lost auctions carrying a winner's price, because a thin week's sample must not
move real-money caps without someone reading `n`.
"""

from __future__ import annotations

import pytest

from rehoboam.services.ceiling_derivation import AuctionRow, derive_price_bands

BANDS = (0, 5_000_000, 15_000_000)


def _row(mv, winner_pct=None, our_pct=10.0, won=False):
    return AuctionRow(
        market_value=mv, our_overbid_pct=our_pct, won=won, winning_overbid_pct=winner_pct
    )


def _rows(n_per_band=12):
    rows = []
    for i in range(n_per_band):
        rows.append(_row(2_000_000, winner_pct=40.0 + i * 5))  # 40..95
        rows.append(_row(8_000_000, winner_pct=10.0 + i * 2))  # 10..32
        rows.append(_row(20_000_000, winner_pct=4.0 + i))  # 4..15
    return rows


class TestTheReport:
    def test_rows_are_bucketed_by_market_value(self):
        report = derive_price_bands(_rows(), bands=BANDS, min_winners=30)
        assert [b.lower for b in report.bands] == [0, 5_000_000, 15_000_000]
        assert [b.n_with_winner for b in report.bands] == [12, 12, 12]

    def test_the_proposal_is_the_winners_p75(self):
        report = derive_price_bands(_rows(), bands=BANDS, min_winners=30)
        top = report.bands[-1]
        # winners 4..15 → p75 = 12.25 (linear interpolation over 12 points)
        assert top.winner_p75 == pytest.approx(12.25)
        assert report.proposal == {
            0: pytest.approx(81.25),
            5_000_000: pytest.approx(26.5),
            15_000_000: pytest.approx(12.25),
        }

    def test_our_bids_and_win_rate_are_reported_beside_them(self):
        rows = _rows() + [_row(20_000_000, our_pct=30.0, won=True)]
        report = derive_price_bands(rows, bands=BANDS, min_winners=30)
        top = report.bands[-1]
        assert top.n_auctions == 13
        assert top.n_won == 1
        assert top.our_median == pytest.approx(10.0)

    def test_rows_without_a_winner_count_as_auctions_but_not_as_evidence(self):
        rows = _rows() + [_row(20_000_000)] * 5
        report = derive_price_bands(rows, bands=BANDS, min_winners=30)
        assert report.bands[-1].n_auctions == 17
        assert report.bands[-1].n_with_winner == 12
        assert report.n_winners == 36


class TestTheRefusal:
    def test_a_thin_sample_proposes_nothing(self):
        report = derive_price_bands(_rows(n_per_band=9), bands=BANDS, min_winners=30)
        assert report.n_winners == 27
        assert report.proposal is None
        # The table is still printed — the reader sees n.
        assert len(report.bands) == 3

    def test_a_band_with_no_winners_is_left_out_of_the_proposal(self):
        rows = [_row(2_000_000, winner_pct=50.0)] * 31
        report = derive_price_bands(rows, bands=BANDS, min_winners=30)
        assert report.proposal == {0: pytest.approx(50.0)}

    def test_the_env_line_is_ready_to_paste(self):
        report = derive_price_bands(_rows(), bands=BANDS, min_winners=30)
        assert report.env_line == "OVERBID_PRICE_BANDS=0:81.2,5000000:26.5,15000000:12.2"

    def test_no_env_line_without_a_proposal(self):
        report = derive_price_bands(_rows(n_per_band=9), bands=BANDS, min_winners=30)
        assert report.env_line is None
