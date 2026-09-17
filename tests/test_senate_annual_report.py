"""Half of Congress had no asset data, because nothing read a Senate annual.

343 of 343 Senate annual reports in the corpus stored **zero assets**, and the
cause was not a bug in any of them. `parse_disclosure` dispatches on the file
suffix, so every Senate filing -- annual reports included -- went to
`parse_senate_html`, which looks for transaction tables. An annual report has
none. It was recorded as an empty parse and nobody ever read one.

Rick Scott's 2024 annual lists 390 holdings in Part 3, including a personal
residence at $25,000,001 - $50,000,000, and one debt in Part 7 at $5,000,001 -
$25,000,000. None of it was in the database.

The markup below is the shape eFD serves, trimmed to the rows each test needs.
It is a real table with named columns, an asset name in its own element, and an
asset class eFD states rather than leaves to be guessed -- better structured than
the House PDFs this project fights with. What it costs is in the value bands:
see `_senate_band` for the two that a generic reader gets exactly wrong.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from src.parsing.senate_html_parser import (
    SenateHtmlParser,
    _senate_asset_type,
    _senate_band,
    count_senate_asset_rows,
)

ANNUAL = """
<html><body>
<h3 class="h4">Part 3. Assets</h3>
<table>
  <thead><tr>
    <th></th><th>Asset</th><th>Asset Type</th><th>Owner</th>
    <th>Value</th><th>Income Type</th><th>Income</th>
  </tr></thead>
  <tbody>
    <tr>
      <td>1</td>
      <td><strong>Personal Residence LLC</strong>
          <div class="muted"><em>Company:</em> Personal Residence LLC (Naples, FL)
          <em>Description:</em> Holding company for personal residence</div></td>
      <td>Business Entity<div class="muted">Limited Liability Company (LLC)</div></td>
      <td>Joint</td><td>--</td><td></td><td></td>
    </tr>
    <tr>
      <td>1.1</td>
      <td><strong>Personal Residence - Naples</strong></td>
      <td>Real Estate<div class="muted">Residential</div></td>
      <td>Joint</td><td>$25,000,001 - $50,000,000</td>
      <td>None</td><td>None (or less than $201)</td>
    </tr>
    <tr>
      <td>2</td>
      <td><strong>Lower Alabama Gas Project Revenue Bond</strong></td>
      <td>Government Securities<div class="muted">Municipal Security</div></td>
      <td>Self</td><td>$100,001 - $250,000</td>
      <td>Interest</td><td>$2,501 - $5,000</td>
    </tr>
    <tr>
      <td>3</td>
      <td><strong>A fund the filer's spouse owns</strong></td>
      <td>Investment Fund<div class="muted">Private Equity Fund</div></td>
      <td>Spouse</td>
      <td>Over $1,000,000 and held independently by spouse or dependent child</td>
      <td>None</td><td>None (or less than $201)</td>
    </tr>
    <tr>
      <td>4</td>
      <td><strong>A small brokerage balance</strong></td>
      <td>Bank Deposit</td>
      <td>Self</td><td>None (or less than $1,001)</td>
      <td>None</td><td>None (or less than $201)</td>
    </tr>
    <tr>
      <td>5</td>
      <td><strong>A trust that states its own total</strong></td>
      <td>Trust<div class="muted">General Trust</div></td>
      <td>Self</td><td>$1,000,001 - $5,000,000</td>
      <td>None</td><td>None (or less than $201)</td>
    </tr>
    <tr>
      <td>5.1</td>
      <td><strong>What is inside that trust</strong></td>
      <td>Mutual Funds<div class="muted">Mutual Fund</div></td>
      <td>Self</td><td>$1,000,001 - $5,000,000</td>
      <td>Excepted Investment Fund</td><td>$2,501 - $5,000</td>
    </tr>
  </tbody>
</table>
<h3 class="h4">Part 7. Liabilities</h3>
<table>
  <thead><tr>
    <th></th><th>#</th><th>Incurred</th><th>Debtor</th><th>Type</th>
    <th>Points</th><th>Rate (Term)</th><th>Amount</th><th>Creditor</th><th>Comments</th>
  </tr></thead>
  <tbody>
    <tr>
      <td></td><td>1</td><td>2024</td><td>Self</td><td>Other (Campaign Loan)</td>
      <td>-</td><td>0% (On demand)</td><td>$5,000,001 - $25,000,000</td>
      <td>Rick Scott for Florida Tampa, FL</td><td>n/a</td>
    </tr>
  </tbody>
</table>
</body></html>
"""

SCAN = """
<html><body>
<img src="https://efd-media-public.senate.gov/search/view/paper/abc_1.gif">
<img src="https://efd-media-public.senate.gov/search/view/paper/abc_2.gif">
</body></html>
"""


def parse(tmp_path: Path, markup: str) -> dict:
    path = tmp_path / "filing.html"
    path.write_text(markup, encoding="utf-8")
    return SenateHtmlParser().parse_senate_annual(str(path))


class TestPartThreeIsRead:
    def test_every_listed_holding_is_stored(self, tmp_path):
        assert len(parse(tmp_path, ANNUAL)["assets"]) == 7

    def test_the_holding_the_site_never_showed(self, tmp_path):
        residence = next(
            a
            for a in parse(tmp_path, ANNUAL)["assets"]
            if a["description"] == "Personal Residence - Naples"
        )
        assert (residence["value_min"], residence["value_max"]) == (
            Decimal(25000001),
            Decimal(50000000),
        )
        assert residence["asset_type"] == "real_estate"

    def test_the_name_is_taken_from_its_own_element(self, tmp_path):
        # The cell also carries eFD's company and description annotations.
        # Reading the whole cell stores "Personal Residence LLC Company:
        # Personal Residence LLC (Naples, FL) Description: Holding company for
        # personal residence" as the name of the asset.
        names = [a["description"] for a in parse(tmp_path, ANNUAL)["assets"]]
        assert "Personal Residence LLC" in names

    def test_a_container_does_not_count_its_own_contents(self, tmp_path):
        # eFD lists a holding company and then, beneath it, what is inside it.
        # Counting both sums the container and its contents -- the one way an
        # asset reader can OVERstate somebody. The row is kept so that what is
        # stored still matches what the filing lists; only the value is set
        # aside.
        assets = {a["description"]: a for a in parse(tmp_path, ANNUAL)["assets"]}
        assert assets["Personal Residence LLC"]["value_min"] is None
        # And the case that actually bites: a container that states a value of
        # its own while its contents are itemised underneath it. Keeping both
        # counts the same money twice.
        assert assets["A trust that states its own total"]["value_min"] is None
        assert assets["What is inside that trust"]["value_min"] == Decimal(1000001)

    def test_the_asset_class_is_read_rather_than_guessed(self, tmp_path):
        # eFD states it. Guessing from the description makes "Lower Alabama Gas
        # Project Revenue Bond" a bond by luck and "Personal Residence - Naples"
        # real estate by luck, and neither is a rule.
        kinds = {a["description"]: a["asset_type"] for a in parse(tmp_path, ANNUAL)["assets"]}
        assert kinds["Lower Alabama Gas Project Revenue Bond"] == "bond"
        assert kinds["A small brokerage balance"] == "bank_account"


class TestPartSevenIsRead:
    def test_the_debt_is_stored_with_its_amount(self, tmp_path):
        assert parse(tmp_path, ANNUAL)["liabilities"] == [
            {
                "creditor": "Rick Scott for Florida Tampa, FL",
                "description": "Other (Campaign Loan)",
                "amount_min": Decimal(5000001),
                "amount_max": Decimal(25000000),
            }
        ]


class TestTheBandsEfdWritesThatAreNotRanges:
    def test_less_than_is_not_an_exact_amount(self):
        # "None (or less than $1,001)" read naively is an exact $1,001. 116 of
        # Rick Scott's 390 holdings carry it, so reading it that way adds
        # $116,116 of wealth his filing explicitly denies.
        assert _senate_band("None (or less than $1,001)") == (Decimal(0), Decimal(1000))

    def test_a_spouses_holding_has_no_upper_bound(self):
        # Senate rules let a filer stop counting at a million for a spouse's
        # separate property. Recording that as a flat $1,000,000 states a number
        # the document does not, and 50 of Scott's holdings carry it.
        low, high = _senate_band(
            "Over $1,000,000 and held independently by spouse or dependent child"
        )
        assert low == Decimal(1000001)
        assert high is None

    def test_an_ordinary_range_is_a_range(self):
        assert _senate_band("$15,001 - $50,000") == (Decimal(15001), Decimal(50000))

    def test_a_dash_is_not_a_value(self):
        assert _senate_band("--") == (None, None)

    def test_an_unknown_band_yields_nothing_rather_than_a_guess(self):
        assert _senate_band("see attachment") == (None, None)

    def test_a_named_class_beats_a_word_in_it(self):
        # "Government Securities Municipal Security" ends in the word
        # "Security", which a shorter rule reads as a stock.
        assert _senate_asset_type("Government Securities Municipal Security") == "bond"


class TestWhatTheScoreIsMeasuredAgainst:
    def test_the_rows_are_counted_from_the_markup(self):
        assert count_senate_asset_rows(ANNUAL) == 7

    def test_a_filing_with_no_part_three_counts_nothing(self):
        assert count_senate_asset_rows("<html><body>nothing here</body></html>") == 0


class TestAScanIsNotAParserFailure:
    def test_page_images_are_reported_as_what_they_are(self, tmp_path):
        result = parse(tmp_path, SCAN)
        assert result["assets"] == []
        assert "scanned paper filing" in result["parse_errors"][0]

    def test_a_scan_records_no_text_layer(self, tmp_path):
        # `score_fd_parse` reads this to tell a scan from a filing the parser
        # failed on. eFD's own chrome makes `bool(text)` true of every page,
        # which says nothing about the filing.
        assert parse(tmp_path, SCAN)["raw_text"] == ""
