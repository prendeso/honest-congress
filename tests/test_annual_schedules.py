"""84% of published House net worth was made of trades, not holdings.

`_parse_assets_section` decided a table was Schedule A with

    if "asset" in header_text or "value" in header_text:

and BOTH schedules on the House annual form open with an Asset column. These
headers are copied from the tables pdfplumber actually returns:

    A  ['Asset','Owner','Value of Asset','Income Type(s)','Income','Tx. > $1,000?']
    B  ['Asset','Owner','Date','Tx. Type','Amount','Cap. Gains > $200?']

So every TRANSACTION in an annual filing was stored as a holding, with the
trade's Amount as the holding's value. `wealth_analyzer` sums `value_min` and
`value_max` over `Asset` rows to build `net_worth_estimate`, which is what
`excessive_wealth_growth` and `wealth_vs_salary` publish dollar figures from.
A member who traded one $15,000 position fifty times gained $750,000 of wealth.

Measured over 40 House annual filings, EVERY ONE scored at confidence 1.0:

    net worth they contribute          $402,905,378
    of which came from Schedule B      $338,590,568   (84.0%)
    'assets' stored                    4,837
    of which were transactions         4,618          (95.5%)

And Schedule A's own rows were being dropped at the same time. pdfplumber
fragments these tables: the schedule header comes back as a table with no data
rows, and each following fragment carries a DATA ROW as its header, which the
test above then skips. Of 2,499 real Schedule A holdings across those filings,
219 were captured -- 8.8%. Rosa DeLauro's filing lists a Schedule A over four
pages and yielded nothing at all.

Live confirmation, from /api/assets before the fix:

    Marjorie Greene  "Marjorie IRA => 05/05/2025 P $1,001 - $15,000
                      JP Morgan Chase &"
"""

from __future__ import annotations

import pytest

from src.parsing.pdf_parser import DisclosureParser

SCHEDULE_A = ["Asset", "Owner", "Value of Asset", "Income Type(s)", "Income", "Tx. >\n$1,000?"]
SCHEDULE_B = ["Asset", "Owner", "Date", "Tx.\nType", "Amount", "Cap.\nGains >\n$200?"]
SCHEDULE_C = ["Source", "Type", "Amount"]
SCHEDULE_D = ["Owner", "Creditor", "Date Incurred", "Type", "Amount of\nLiability"]
SCHEDULE_E = ["Position", "Name of Organization"]


def a_holding(name="Paychex, Inc. - Common Stock (PAYX) [ST]", value="$1,001 - $15,000"):
    return [name, "JT", value, "Dividends", "$201 -\n$1,000", ""]


def a_trade(name="Paychex, Inc. - Common Stock (PAYX) [ST]", amount="$1,001 - $15,000"):
    return [name, "DC", "02/09/2025", "P", amount, ""]


@pytest.fixture(scope="module")
def parser():
    return DisclosureParser()


class TestATradeIsNotAHolding:
    def test_a_schedule_b_row_is_not_stored_as_an_asset(self, parser):
        tables = [[SCHEDULE_B, a_trade(), a_trade(amount="$15,001 - $50,000")]]

        assert parser._parse_assets_section("", tables) == []

    def test_the_same_row_under_schedule_a_is_stored(self, parser):
        """The rows are near-identical; only the schedule says which is which."""
        tables = [[SCHEDULE_A, a_holding()]]

        assets = parser._parse_assets_section("", tables)

        assert len(assets) == 1
        assert assets[0]["value_min"] == 1001

    def test_trades_add_nothing_to_net_worth(self, parser):
        """The number that mattered: what `wealth_analyzer` would sum."""
        tables = [
            [SCHEDULE_A, a_holding(value="$1,001 - $15,000")],
            [SCHEDULE_B] + [a_trade(amount="$50,001 - $100,000")] * 50,
        ]

        assets = parser._parse_assets_section("", tables)
        total = sum(
            (float(a["value_min"]) + float(a["value_max"])) / 2
            for a in assets
            if a.get("value_min") is not None and a.get("value_max") is not None
        )

        assert total == pytest.approx(8000.5)

    def test_a_fragment_of_schedule_b_is_not_an_asset_either(self, parser):
        """Fragments carry no header, so only document order says they are
        trades. This is the case that makes reading tables in isolation unsafe."""
        tables = [
            [SCHEDULE_B],
            [a_trade(), a_trade(name="IDEXX Laboratories, Inc. (IDXX) [ST]")],
        ]

        assert parser._parse_assets_section("", tables) == []


class TestFragmentedScheduleARowsAreRecovered:
    def test_a_header_with_no_rows_followed_by_fragments(self, parser):
        """Exactly what pdfplumber returns for these filings: the header alone,
        then each holding as its own table with the data row as the header."""
        tables = [
            [SCHEDULE_A],
            [a_holding(name="Bank of America [BA]")],
            [a_holding(name="Nationwide [FN]", value="$209,630.10")],
        ]

        assets = parser._parse_assets_section("", tables)

        assert [a["description"] for a in assets] == [
            "Bank of America [BA]",
            "Nationwide [FN]",
        ]

    def test_a_fragment_stops_at_the_next_schedule(self, parser):
        tables = [
            [SCHEDULE_A],
            [a_holding(name="Real Holding [ST]")],
            [SCHEDULE_D],
            [["JT", "Bank of America", "7/29/1999", "Mortgage", "$50,001 -\n$100,000"]],
        ]

        assets = parser._parse_assets_section("", tables)

        assert [a["description"] for a in assets] == ["Real Holding [ST]"]

    def test_rows_before_any_header_are_not_guessed_at(self, parser):
        """With no schedule open there is nothing to attribute a fragment to."""
        tables = [[a_holding(name="Orphan [ST]")]]

        assert parser._parse_assets_section("", tables) == []


class TestTheFormsOwnContinuationLinesAreNotHoldings:
    @pytest.mark.parametrize(
        "line",
        [
            "L: NY",
            "D: Independent professionally managed account.",
            "C: Income from consulting. No investment income.",
            "L: Washington, DC, US",
        ],
    )
    def test_a_continuation_line_is_not_an_asset(self, parser, line):
        """One filing stored 112 copies of "D: Independent professionally
        managed account." as holdings."""
        tables = [[SCHEDULE_A], [[line, "", "", "", "", ""]]]

        assert parser._parse_assets_section("", tables) == []

    def test_a_real_holding_beginning_with_a_letter_is_kept(self, parser):
        """The guard is the "X: " shape, not a letter at the start."""
        tables = [[SCHEDULE_A, a_holding(name="L3Harris Technologies (LHX) [ST]")]]

        assets = parser._parse_assets_section("", tables)

        assert [a["description"] for a in assets] == ["L3Harris Technologies (LHX) [ST]"]


class TestTheTextFallbackDoesNotDoubleCount:
    def test_it_does_not_run_when_the_tables_yielded_holdings(self, parser):
        """It used to run unconditionally and append to whatever the tables
        gave, which is double counting by construction."""
        text = "Apple Inc Common Stock - $1,001 - $15,000\n"
        tables = [[SCHEDULE_A, a_holding()]]

        assets = parser._parse_assets_section(text, tables)

        assert len(assets) == 1

    def test_it_still_runs_when_there_are_no_tables_at_all(self, parser):
        text = "Vanguard Index Fund - $15,001 - $50,000\n"

        assets = parser._parse_assets_section(text, [])

        assert len(assets) == 1


class TestOtherSchedulesAreNotHoldings:
    @pytest.mark.parametrize(
        "header,row",
        [
            (SCHEDULE_C, ["TIAA/CREF", "Retirement Income", "$4,665.72"]),
            (SCHEDULE_E, ["Honorary Board Member", "Special Olympics"]),
            (
                SCHEDULE_D,
                ["JT", "Bank of America", "7/29/1999", "Mortgage", "$250,001 -\n$500,000"],
            ),
        ],
    )
    def test_a_non_asset_schedule_yields_no_holdings(self, parser, header, row):
        assert parser._parse_assets_section("", [[header, row]]) == []


class TestAnExactAmountIsNotARange:
    """`([\\d,]+)` stopped at the decimal point, so an exact figure came back as
    two numbers and was read as the range between them. Filers report exact
    values for bank balances and credit-card debts, and both feed the net-worth
    sums `excessive_wealth_growth` publishes."""

    @pytest.mark.parametrize(
        "text,low,high",
        [
            # Rosa DeLauro's Nationwide holding, and her American Express debt.
            ("$209,630.10", "209630.10", "209630.10"),
            ("$16,508.00", "16508.00", "16508.00"),
            ("$4,665.72", "4665.72", "4665.72"),
        ],
    )
    def test_cents_are_not_read_as_the_lower_bound(self, parser, text, low, high):
        from decimal import Decimal

        assert parser._parse_value_range(text) == (Decimal(low), Decimal(high))

    def test_a_debt_is_never_reported_as_possibly_zero(self, parser):
        """ "$16,508.00" gave a minimum of 0 -- so the widest defensible reading
        of that filer's net worth had the debt vanishing entirely."""
        low, _ = parser._parse_value_range("$16,508.00")

        assert low > 0

    @pytest.mark.parametrize(
        "text,low,high",
        [
            ("$1,001 - $15,000", "1001", "15000"),
            ("$1,000,001 -\n$5,000,000", "1000001", "5000000"),
            ("$50,000", "50000", "50000"),
        ],
    )
    def test_a_real_range_is_unchanged(self, parser, text, low, high):
        from decimal import Decimal

        assert parser._parse_value_range(text) == (Decimal(low), Decimal(high))


class TestLiabilitiesAreReadTheSameWay:
    """Schedule D fragments exactly as Schedule A does, and was missed the same
    way: the header table carries no data rows, and each debt arrives as its own
    table with a data row where the header should be.

    This direction is the one that flatters. `_calculate_wealth_progression`
    SUBTRACTS liabilities, so a debt the parser cannot see RAISES the member's
    apparent net worth -- and net worth is what `excessive_wealth_growth`
    publishes. Rosa DeLauro discloses a $250,001-$500,000 mortgage and a
    $16,508 card balance; the database had neither.
    """

    def a_debt(self, creditor="Bank of America Wilmington, DE", amount="$250,001 -\n$500,000"):
        return ["JT", creditor, "7/29/1999", "Mortgage on Personal Residence", amount]

    def test_a_fragmented_debt_is_recovered(self, parser):
        tables = [[SCHEDULE_D], [self.a_debt()], [self.a_debt(creditor="American Express")]]

        liabilities = parser._parse_liabilities_section("", tables)

        assert [x["creditor"] for x in liabilities] == [
            "Bank of America Wilmington, DE",
            "American Express",
        ]

    def test_the_owner_code_is_not_stored_as_the_creditor(self, parser):
        """Schedule D is `Owner | Creditor | Date | Type | Amount`, so reading
        row[0] filed every debt against a creditor named "JT" or "SP"."""
        liabilities = parser._parse_liabilities_section("", [[SCHEDULE_D, self.a_debt()]])

        assert liabilities[0]["creditor"] == "Bank of America Wilmington, DE"
        assert liabilities[0]["creditor"] not in {"JT", "SP", "DC", "SELF"}

    def test_the_date_is_not_stored_as_the_description(self, parser):
        """ "Date Incurred" sits between the creditor and the type, so the first
        non-amount cell described every mortgage as "7/29/1999"."""
        liabilities = parser._parse_liabilities_section("", [[SCHEDULE_D, self.a_debt()]])

        assert liabilities[0]["description"] == "Mortgage on Personal Residence"

    def test_the_amount_survives(self, parser):
        liabilities = parser._parse_liabilities_section("", [[SCHEDULE_D, self.a_debt()]])

        assert liabilities[0]["amount_min"] == 250001
        assert liabilities[0]["amount_max"] == 500000

    def test_an_exact_balance_is_not_a_range_from_its_cents(self, parser):
        tables = [[SCHEDULE_D, self.a_debt(creditor="American Express", amount="$16,508.00")]]

        liabilities = parser._parse_liabilities_section("", tables)

        assert liabilities[0]["amount_min"] == liabilities[0]["amount_max"] == 16508

    def test_a_row_without_an_owner_code_still_reads_its_creditor(self, parser):
        """Not every filing prints the owner column; the creditor is then first."""
        tables = [[SCHEDULE_D, ["Some Bank", "1/1/2020", "Mortgage", "$50,001 - $100,000"]]]

        liabilities = parser._parse_liabilities_section("", tables)

        assert liabilities[0]["creditor"] == "Some Bank"

    def test_holdings_are_not_collected_as_debts(self, parser):
        assert parser._parse_liabilities_section("", [[SCHEDULE_A, a_holding()]]) == []

    def test_trades_are_not_collected_as_debts(self, parser):
        assert parser._parse_liabilities_section("", [[SCHEDULE_B, a_trade()]]) == []
