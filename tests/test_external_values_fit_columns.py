"""Values from outside must fit the columns they are stored in.

This has now cost two production runs in one night, in two different tables,
for the same reason: PostgreSQL enforces declared string lengths and SQLite
ignores them, so the whole test suite is blind to it.

    disclosures.filing_type   VARCHAR(50)
      <a href="/search/view/annual/14c0.../">Annual Report for CY 2023 ...</a>
      -> the Senate ingest aborted

    campaign_donations.transaction_type  VARCHAR(50)
      CONTRIBUTION RECEIVED FROM REGISTERED FILER (CANDIDATES)   (55 chars)
      -> seventeen minutes of FEC calls committed nothing, and donor_conflict
         had no data to fire on while its workflow step reported success

The second one is the argument for this file. A guard written only for the table
that failed first does not stop the next table, and every one of these columns
holds text this project does not control: filer names, FEC vocabularies, SEC
descriptions, eFD markup.
"""

from __future__ import annotations

import pytest

from src.db.models import Base

# Columns holding text from an external source, with a value long enough to
# have broken them. Not exhaustive by design: it names the fields whose content
# is decided by someone else's API.
EXTERNAL_TEXT = {
    ("campaign_donations", "transaction_type"): (
        "CONTRIBUTION RECEIVED FROM REGISTERED FILER (CANDIDATES)"
    ),
    ("campaign_donations", "donor_name"): (
        "TEXAS INSTRUMENTS INCORPORATED POLITICAL ACTION COMMITTEE (TI PAC)"
    ),
    ("disclosures", "filing_type"): "Annual Report for CY 2024 (Amendment 1)",
    ("lobbying_disclosures", "source"): "senate-lda",
    ("government_contracts", "source"): "usaspending",
    ("government_contracts", "agency"): "Department of Health and Human Services",
    ("company_industries", "sector"): "Pharmaceutical Preparations",
}


def _column(table_name: str, column_name: str):
    table = Base.metadata.tables[table_name]
    return table.columns[column_name]


class TestKnownExternalValuesFit:
    @pytest.mark.parametrize(("target", "value"), sorted(EXTERNAL_TEXT.items()))
    def test_a_real_value_fits_its_column(self, target, value):
        table_name, column_name = target
        column = _column(table_name, column_name)
        limit = getattr(column.type, "length", None)

        assert limit, f"{table_name}.{column_name} has no declared length"
        assert len(value) <= limit, (
            f"{table_name}.{column_name} is VARCHAR({limit}) but a real value from "
            f"the source is {len(value)} characters: {value!r}. PostgreSQL will "
            "refuse the insert and take the whole ingest with it; SQLite will not "
            "tell you."
        )


class TestTheFecVocabularyFits:
    """The specific regression. FEC's transaction type is prose, not a code."""

    # Every value the FEC returns for these contributions, longest first.
    FEC_TRANSACTION_TYPES = (
        "CONTRIBUTION RECEIVED FROM REGISTERED FILER (CANDIDATES)",
        "EARMARKED CONTRIBUTION RECEIVED FROM INDIVIDUAL VIA CONDUIT",
        "CONTRIBUTION RECEIVED FROM INDIVIDUAL",
        "TRANSFER FROM AFFILIATED COMMITTEE",
    )

    @pytest.mark.parametrize("value", FEC_TRANSACTION_TYPES)
    def test_it_fits(self, value):
        limit = _column("campaign_donations", "transaction_type").type.length

        assert len(value) <= limit, (
            f"{value!r} is {len(value)} characters and the column holds {limit}"
        )

    def test_the_column_was_actually_widened(self):
        """Guards against the migration being reverted while the tests above
        keep passing on a coincidentally shorter sample."""
        assert _column("campaign_donations", "transaction_type").type.length >= 255


class TestTheContractActionKeyFits:
    """`government_contracts.external_id` stopped being an opaque number.

    It used to hold USASpending's `internal_id`, which is short and identifies
    the contract rather than the obligation -- so nine separate award actions
    collapsed into one row and the eight dropped carried the action dates the
    front-run detector reads. The key is now composite:

        <PIID>|<modification>|<action date>|<amount>

    which is longer, and the column it goes in is VARCHAR(100). That number is
    not comfortable by accident: the longest key over 1,200 live transaction
    rows is 47 characters, and the worst case is bounded by the PIID, which the
    FAR caps at 50.
    """

    # The longest real key measured against the live API.
    LONGEST_SEEN = "70SBUR24F00000103|P00016|2026-08-06|63696545.58"

    # PIID at its FAR maximum, a six-character modification, a date, and an
    # amount wider than any federal contract has ever been.
    WORST_CASE = "|".join(("P" * 50, "P00016", "2026-08-06", "999999999999999.99"))

    @pytest.mark.parametrize("value", (LONGEST_SEEN, WORST_CASE))
    def test_it_fits(self, value):
        limit = _column("government_contracts", "external_id").type.length
        assert len(value) <= limit, (
            f"a contract action key of {len(value)} characters does not fit "
            f"VARCHAR({limit}): {value!r}. PostgreSQL will refuse the insert and "
            "take the whole contract ingest with it."
        )

    def test_the_key_the_ingester_builds_is_the_shape_assumed_here(self):
        from src.ingestion.usaspending import award_action_key

        assert (
            award_action_key(
                {
                    "Award ID": "70SBUR24F00000103",
                    "Mod": "P00016",
                    "Action Date": "2026-08-06",
                    "Transaction Amount": 63696545.58,
                }
            )
            == self.LONGEST_SEEN
        )


class TestEveryExternallyFedTextColumnHasRoom:
    """A blanket floor. Anything holding third-party prose in 50 characters or
    fewer is a future abort waiting for a longer value, so it has to be
    deliberate rather than accidental."""

    # Columns that are genuinely short codes, not prose, with the reason.
    KNOWN_SHORT = {
        ("members", "bioguide_id"): "a 7-character identifier",
        ("members", "chamber"): "an enum",
        ("members", "party"): "an enum",
        ("members", "state"): "a 2-letter code",
        ("members", "district"): "a district number",
        ("transactions", "transaction_type"): "an enum",
        ("transactions", "ticker"): "a ticker symbol",
        ("transactions", "owner"): "Self / Spouse / Joint / Dependent Child",
        ("transactions", "filing_status"): "New / Amended, normalised on read",
        ("assets", "asset_type"): "an enum",
        ("assets", "ticker"): "a ticker symbol",
        ("assets", "income_type"): "a short income category",
        ("liabilities", "liability_type"): "a short liability category",
        ("anomalies", "anomaly_type"): "our own vocabulary",
        ("anomalies", "severity"): "low / medium / high",
        ("bills", "bill_type"): "hr, s, hjres and similar",
        ("bills", "number"): "a bill number",
        ("bills", "origin_chamber"): "House or Senate",
        ("bill_committees", "committee_id"): "a committee code",
        ("bill_committees", "chamber"): "House or Senate",
        ("committee_assignments", "committee_id"): "a committee code",
        ("committee_assignments", "parent_committee_id"): "a committee code",
        ("committee_assignments", "chamber"): "an enum",
        ("committee_assignments", "party"): "majority / minority",
        ("company_industries", "ticker"): "a ticker symbol",
        ("company_industries", "sic"): "a 4-digit SIC code",
        ("company_industries", "sector"): "our own mapped sector names",
        ("campaign_donations", "ticker"): "a ticker symbol",
        ("campaign_donations", "cycle"): "a 4-digit year",
        ("campaign_donations", "source"): "our own source label",
        ("government_contracts", "ticker"): "a ticker symbol",
        ("government_contracts", "source"): "our own source label",
        ("lobbying_disclosures", "ticker"): "a ticker symbol",
        ("lobbying_disclosures", "source"): "our own source label",
        ("disclosures", "filing_type"): "a filing-type label we normalise ourselves",
    }

    def test_every_short_column_is_accounted_for(self):
        unexplained = []
        for table in Base.metadata.sorted_tables:
            for column in table.columns:
                limit = getattr(column.type, "length", None)
                if limit and limit <= 50:
                    key = (table.name, column.name)
                    if key not in self.KNOWN_SHORT:
                        unexplained.append(f"{table.name}.{column.name} VARCHAR({limit})")

        assert not unexplained, (
            "these hold 50 characters or fewer and are not listed as deliberate "
            "short codes. If one stores third-party prose it will abort an ingest "
            "on PostgreSQL and pass every test on SQLite:\n  " + "\n  ".join(unexplained)
        )

    def test_the_inventory_is_not_stale(self):
        """The other direction: a column that no longer exists should not sit in
        the list pretending to be covered."""
        gone = [
            f"{t}.{c}"
            for (t, c) in self.KNOWN_SHORT
            if t not in Base.metadata.tables or c not in Base.metadata.tables[t].columns
        ]

        assert not gone, f"listed but no longer in the schema: {gone}"
