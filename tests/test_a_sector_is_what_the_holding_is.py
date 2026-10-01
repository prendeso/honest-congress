"""Three ways a holding was given a sector it does not have.

Found by checking live committee-bill and sponsorship findings against the
filings they cite:

* Gold trusts as finance. GLD, IAU and SLV file under SIC 6221, commodity
  contracts dealers, which the 62xx rule called finance. 221 live finance
  findings named nothing but gold trusts.
* Fund families as finance. "jpmorgan" and "goldman" were finance keywords, so
  "JPMorgan Large Cap Growth Fund" and a Goldman international equity fund were
  banking exposure.
* Electrical equipment as technology. Every 36xx code was technology, and GE and
  GE Vernova file at 3600.
"""

from __future__ import annotations

import pytest

from src.analysis.sectors import SectorIndex, classify, sector_for_sic


class TestCommodityTrusts:
    def test_a_commodity_dealer_code_is_no_sector(self):
        assert sector_for_sic("6221") == frozenset()

    def test_the_rest_of_62xx_is_still_finance(self):
        assert sector_for_sic("6211") == {"finance"}

    @pytest.mark.parametrize(
        ("ticker", "name"), [("GLD", "SPDR Gold Trust"), ("IAU", "iShares Gold Trust")]
    )
    def test_a_gold_trust_is_not_finance(self, ticker, name):
        assert SectorIndex({ticker: "6221"}).classify(ticker, name) == set()


class TestFundFamilies:
    @pytest.mark.parametrize(
        "description",
        [
            "JPMorgan Large Cap Growth Fund - Class I (HLGEX)",
            "JPMorgan Equity Premium Income ETF",
            "Goldman Sachs International Equity Insights Fund Institutional",
        ],
    )
    def test_a_fund_named_for_its_manager_is_not_finance(self, description):
        assert "finance" not in classify(None, description)

    @pytest.mark.parametrize(
        "description", ["JPMorgan Chase & Co. Common Stock", "Goldman Sachs Group Inc"]
    )
    def test_the_bank_itself_still_is(self, description):
        assert classify(None, description) == {"finance"}

    def test_a_sector_fund_still_classifies_by_its_sector(self):
        assert classify(None, "Vanguard Health Care Fund Admiral") == {"healthcare"}
        assert classify(None, "SPDR S&P Bank ETF") == {"finance"}


class TestElectricalEquipment:
    @pytest.mark.parametrize("sic", ["3600", "3612", "3630", "3690"])
    def test_electrical_equipment_is_not_technology(self, sic):
        assert "technology" not in sector_for_sic(sic)

    @pytest.mark.parametrize("sic", ["3674", "3672", "3571", "7372"])
    def test_semiconductors_computers_and_software_still_are(self, sic):
        assert "technology" in sector_for_sic(sic)

    def test_communications_equipment_keeps_both_labels(self):
        assert sector_for_sic("3663") == {"technology", "telecom"}
