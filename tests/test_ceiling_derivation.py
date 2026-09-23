"""`derive-ceilings`: propose price-band overbid caps from what the league paid.

Pure. The command reads `transfer_premiums` (every buyer's completed buys,
priced against the day's market value) and `auction_outcomes` (the auctions
WE entered), hands both here, prints what comes back and changes nothing.
Rival offers are never visible, so completed transfers are the only evidence
of what it takes to win. A band's proposal is the 75th percentile of the
winners' premium in that band, and the whole proposal is withheld below
`min_winners` priced buys, because a thin sample must not move real-money
caps without someone reading `n`.
"""

from __future__ import annotations

import pytest

from rehoboam.services.ceiling_derivation import OurBid, WinnerRow, derive_price_bands

BANDS = (0, 5_000_000, 15_000_000)


def _winners(n_per_band=12):
    rows = []
    for i in range(n_per_band):
        rows.append(WinnerRow(2_000_000, 40.0 + i * 5))  # 40..95
        rows.append(WinnerRow(8_000_000, 10.0 + i * 2))  # 10..32
        rows.append(WinnerRow(20_000_000, 4.0 + i))  # 4..15
    return rows


class TestTheReport:
    def test_winners_are_bucketed_by_market_value(self):
        report = derive_price_bands(_winners(), bands=BANDS, min_winners=30)
        assert [b.lower for b in report.bands] == [0, 5_000_000, 15_000_000]
        assert [b.n_winners for b in report.bands] == [12, 12, 12]

    def test_the_proposal_is_the_winners_p75(self):
        report = derive_price_bands(_winners(), bands=BANDS, min_winners=30)
        assert report.bands[-1].winner_p75 == pytest.approx(12.25)
        assert report.proposal == {
            0: pytest.approx(81.25),
            5_000_000: pytest.approx(26.5),
            15_000_000: pytest.approx(12.25),
        }

    def test_our_own_bids_are_reported_beside_the_league(self):
        ours = [
            OurBid(20_000_000, 30.0, won=True),
            OurBid(21_000_000, 24.0, won=False),
            OurBid(3_000_000, 10.0, won=False),
        ]
        report = derive_price_bands(_winners(), bands=BANDS, min_winners=30, our_bids=ours)
        top = report.bands[-1]
        assert top.n_our_bids == 2
        assert top.n_our_wins == 1
        assert top.our_median == pytest.approx(27.0)
        assert report.bands[0].n_our_bids == 1

    def test_our_bids_never_count_as_winners(self):
        ours = [OurBid(20_000_000, 30.0, won=True)] * 40
        report = derive_price_bands([], bands=BANDS, min_winners=30, our_bids=ours)
        assert report.n_winners == 0
        assert report.proposal is None


class TestTheRefusal:
    def test_a_thin_sample_proposes_nothing(self):
        report = derive_price_bands(_winners(n_per_band=9), bands=BANDS, min_winners=30)
        assert report.n_winners == 27
        assert report.proposal is None
        assert len(report.bands) == 3  # the table is still printed

    def test_a_band_with_no_winners_is_left_out_of_the_proposal(self):
        rows = [WinnerRow(2_000_000, 50.0)] * 31
        report = derive_price_bands(rows, bands=BANDS, min_winners=30)
        assert report.proposal == {0: pytest.approx(50.0)}

    def test_a_buy_below_the_first_band_is_ignored(self):
        rows = [WinnerRow(1_000_000, 50.0)] * 31
        report = derive_price_bands(rows, bands=(5_000_000,), min_winners=30)
        assert report.n_winners == 0

    def test_the_env_line_is_ready_to_paste(self):
        report = derive_price_bands(_winners(), bands=BANDS, min_winners=30)
        assert report.env_line == "OVERBID_PRICE_BANDS=0:81.2,5000000:26.5,15000000:12.2"

    def test_no_env_line_without_a_proposal(self):
        report = derive_price_bands(_winners(n_per_band=9), bands=BANDS, min_winners=30)
        assert report.env_line is None
