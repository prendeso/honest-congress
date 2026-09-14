"""Senate coverage was zero, and the code reported that as success.

All 485 findings on the live site are `chamber: house`. That is not a data
glitch — it is the exact output this code was written to produce. Four
independent defects, each sufficient on its own:

1. The agreement was sent as `GET /search/home/?accept=true`. eFD requires
   `POST prohibition_agreement=1`. Unaccepted sessions get 503 on every search.
2. The search sent `report_type` / `filer_type` as singular scalars. eFD wants
   `report_types` / `filer_types` as JSON arrays. It answers 200 either way and
   simply matches nothing.
3. The row parser read the LAST cell as the document link — that cell is the
   date; the link is in cell 3 — and required a numeric id via `(\\d+)`, where
   eFD uses UUIDs. Measured against 100 live rows, it matched zero.
4. Trade reports were never requested at all, and nothing set `is_ptr`, so any
   that did arrive would have gone to the annual-filing parser and been
   invisible to the late-filing detector.

And every failure path returned `[]`, so an outage and an empty year were the
same value. "Synced 0 Senate disclosures" exited green for months.

The fixture is a real eFD response captured from the live service (
`tests/fixtures/senate_efd_search.json`). Tests run against it rather than the
network so they are deterministic, but it is genuine data: the old parser scores
0 on it and the new one scores 10/10.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingestion.senate import (
    FILER_TYPE_SENATOR,
    REPORT_TYPE_ANNUAL,
    REPORT_TYPE_PTR,
    SenateIngester,
    SenateSearchError,
    _as_json_array,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "senate_efd_search.json"


@pytest.fixture(scope="module")
def captured():
    payload = json.loads(FIXTURE.read_text())
    assert payload["ptr"]["data"], "fixture holds no PTR rows — tests would pass on nothing"
    assert payload["annual"]["data"], "fixture holds no annual rows"
    return payload


class TestTheParserReadsRealRows:
    def test_every_trade_report_row_parses(self, captured):
        rows = SenateIngester()._parse_ajax_results({"data": captured["ptr"]["data"]}, 2025)

        assert len(rows) == len(captured["ptr"]["data"]), (
            "rows were dropped; the old parser scored 0 here by reading the date "
            "cell as the link and demanding a numeric id"
        )

    def test_every_annual_row_parses(self, captured):
        rows = SenateIngester()._parse_ajax_results({"data": captured["annual"]["data"]}, 2025)

        assert len(rows) == len(captured["annual"]["data"])

    def test_the_document_id_is_the_uuid_not_a_run_of_digits(self, captured):
        rows = SenateIngester()._parse_ajax_results({"data": captured["ptr"]["data"]}, 2025)

        for row in rows:
            assert len(row["document_id"]) > 20, row["document_id"]
            assert "-" in row["document_id"], f"{row['document_id']} does not look like an eFD UUID"

    def test_the_url_points_at_efd(self, captured):
        rows = SenateIngester()._parse_ajax_results({"data": captured["ptr"]["data"]}, 2025)

        for row in rows:
            assert row["document_url"].startswith("https://efdsearch.senate.gov/search/view/")

    def test_names_and_dates_survive(self, captured):
        rows = SenateIngester()._parse_ajax_results({"data": captured["ptr"]["data"]}, 2025)

        assert all(row["last_name"] for row in rows)
        assert all(row["filing_date"] is not None for row in rows)


class TestTheFixtureWouldCatchARegression:
    """Proof that the fixture is real data and not a shape invented to pass.

    The old parser's exact logic, run against it, matches nothing. Without this
    the suite above could be satisfied by a fixture built to fit the new code.
    """

    def test_the_previous_logic_scores_zero_on_this_data(self, captured):
        import re as _re

        matched = 0
        rows = captured["ptr"]["data"] + captured["annual"]["data"]
        for row in rows:
            link_html = str(row[-1]) if row else ""  # the old code read the LAST cell
            if _re.search(r"/search/view/paper/(\d+)/", link_html):  # and demanded digits
                matched += 1

        assert rows, "no rows in the fixture"
        assert matched == 0, (
            f"the old parser matched {matched} rows, so this fixture does not "
            "reproduce the failure it was captured to demonstrate"
        )

    def test_the_current_logic_reads_all_of_them(self, captured):
        rows = captured["ptr"]["data"] + captured["annual"]["data"]

        parsed = SenateIngester()._parse_ajax_results({"data": rows}, 2025)

        assert len(parsed) == len(rows)


class TestTradeReportsAreMarkedAsSuch:
    def test_every_ptr_row_is_flagged(self, captured):
        rows = SenateIngester()._parse_ajax_results({"data": captured["ptr"]["data"]}, 2025)

        assert all(row["is_ptr"] for row in rows), (
            "a trade report not flagged is_ptr goes to the annual-filing parser "
            "and is invisible to the late-filing detector"
        )

    def test_a_paper_filed_trade_report_still_counts(self, captured):
        """The subtlety that a first pass at this got wrong.

        The URL segment is the FORMAT, not the report kind: eFD serves
        /view/ptr/ for an electronic filing and /view/paper/ for a scan. A
        scanned trade report is still a trade report. The captured PTR page
        contains both, so keying on the segment alone mislabels the scans.
        """
        rows = SenateIngester()._parse_ajax_results({"data": captured["ptr"]["data"]}, 2025)

        paper = [r for r in rows if "/view/paper/" in r["document_url"]]
        assert paper, "fixture no longer contains a paper-filed PTR; this test is vacuous"
        assert all(r["is_ptr"] for r in paper), (
            "a paper-filed trade report was classified as an annual filing"
        )

    def test_annual_filings_are_not_flagged(self, captured):
        rows = SenateIngester()._parse_ajax_results({"data": captured["annual"]["data"]}, 2025)

        assert not any(row["is_ptr"] for row in rows)


class TestParsedRowsFitTheColumnsTheyAreStoredIn:
    """SQLite ignores declared string lengths. PostgreSQL does not.

    `filing_type` was taken straight from the cell containing the link -- which
    is markup, not a label -- and the first real Senate ingest died on

        DataError: value too long for type character varying(50)
        filing_type: '<a href="/search/view/annual/14c0.../">Annual Report for
        CY 2023 (Amendment 1)</a>'

    Every test in this file passed, because the whole suite runs on SQLite,
    which accepts an over-length string without complaint. This is the same
    blind spot that hid the NUL byte, so the check is written against the
    model's own column widths rather than against one field.
    """

    def _string_limits(self):
        from src.db.models import Disclosure

        limits = {}
        for column in Disclosure.__table__.columns:
            length = getattr(column.type, "length", None)
            if length:
                limits[column.name] = length
        assert limits, "no bounded string columns found — this test would pass on nothing"
        return limits

    def test_no_field_exceeds_its_column(self, captured):
        limits = self._string_limits()
        rows = SenateIngester()._parse_ajax_results(
            {"data": captured["ptr"]["data"] + captured["annual"]["data"]}, 2025
        )
        assert rows

        for row in rows:
            for field, limit in limits.items():
                value = row.get(field)
                if not isinstance(value, str):
                    continue
                assert len(value) <= limit, (
                    f"{field}={value!r} is {len(value)} characters; the column holds "
                    f"{limit} and PostgreSQL will refuse the insert"
                )

    def test_filing_type_is_a_label_not_markup(self, captured):
        rows = SenateIngester()._parse_ajax_results(
            {"data": captured["ptr"]["data"] + captured["annual"]["data"]}, 2025
        )

        for row in rows:
            assert "<" not in row["filing_type"], (
                f"filing_type carries HTML: {row['filing_type']!r}"
            )

    def test_it_still_says_something_useful(self, captured):
        """The guard against fixing this by storing a constant."""
        annual = SenateIngester()._parse_ajax_results({"data": captured["annual"]["data"]}, 2025)
        ptr = SenateIngester()._parse_ajax_results({"data": captured["ptr"]["data"]}, 2025)

        assert all(r["filing_type"] == "PTR" for r in ptr)
        assert any("Annual" in r["filing_type"] for r in annual), (
            f"the annual label lost its meaning: {sorted({r['filing_type'] for r in annual})}"
        )


class TestTheRequestShape:
    @pytest.mark.parametrize(
        "given,expected",
        [("11", "[11]"), ("[11]", "[11]"), ("", "[]"), ("7,11", "[7,11]"), ("  1 ", "[1]")],
    )
    def test_codes_become_json_arrays(self, given, expected):
        assert _as_json_array(given) == expected

    def test_the_codes_are_the_ones_efd_publishes(self):
        """Read from the live search form, not guessed."""
        assert (REPORT_TYPE_ANNUAL, REPORT_TYPE_PTR, FILER_TYPE_SENATOR) == ("7", "11", "1")

    def test_both_report_types_are_requested(self, monkeypatch):
        """Trade reports were never asked for at all."""
        asked = []

        def fake(self, year, filer_type="1", report_type="", start=0, length=100):
            asked.append(report_type)
            return []

        monkeypatch.setattr(SenateIngester, "search_disclosures_ajax", fake)
        SenateIngester().search_all_disclosures(2025)

        assert set(asked) == {REPORT_TYPE_ANNUAL, REPORT_TYPE_PTR}, asked


class TestPaginationReachesEverything:
    def test_it_keeps_asking_until_a_short_page(self, monkeypatch):
        """2025 alone holds 141 trade reports and 119 annual filings, so a single
        page silently truncates the year."""
        pages = {0: 100, 100: 41}

        def fake(self, year, filer_type="1", report_type="", start=0, length=100):
            count = pages.get(start, 0)
            return [{"document_id": f"{report_type}-{start}-{i}"} for i in range(count)]

        monkeypatch.setattr(SenateIngester, "search_disclosures_ajax", fake)
        monkeypatch.setattr("src.ingestion.senate.time.sleep", lambda _s: None)

        rows = SenateIngester().search_all_disclosures(2025)

        # 141 per report type, two report types.
        assert len(rows) == 282, len(rows)

    def test_it_cannot_loop_forever(self, monkeypatch):
        """A server that ignores `start` would otherwise page until the heat
        death of the runner."""

        def always_full(self, year, filer_type="1", report_type="", start=0, length=100):
            return [{"document_id": f"x{i}"} for i in range(length)]

        monkeypatch.setattr(SenateIngester, "search_disclosures_ajax", always_full)
        monkeypatch.setattr("src.ingestion.senate.time.sleep", lambda _s: None)

        rows = SenateIngester().search_all_disclosures(2025)

        assert len(rows) == 2 * 50 * 100, "the page ceiling did not hold"


class TestAnOutageIsNotAnEmptyYear:
    def test_a_failed_session_raises_rather_than_returning_nothing(self, monkeypatch):
        monkeypatch.setattr(SenateIngester, "_init_session", lambda self: False)

        with pytest.raises(SenateSearchError):
            SenateIngester().search_disclosures_ajax(2025)

    def test_the_orchestrator_survives_it_without_claiming_zero(self, db_session, tmp_path):
        """The House data must not be lost with it -- but the run has to say so."""
        from src.ingestion.orchestrator import IngestionOrchestrator

        orch = IngestionOrchestrator(data_dir=tmp_path)

        def boom(year):
            raise SenateSearchError("eFD returned HTTP 503")

        orch.senate.search_all_disclosures = boom  # type: ignore[method-assign]

        assert orch.sync_senate_disclosures(db_session, 2025) == 0
        assert orch.senate_unavailable is True, (
            "an outage was recorded as a year in which the Senate filed nothing"
        )


class TestSenatorsAreMatchedDespiteNameDrift:
    """eFD reports the legal name; the roster reports the known one.

    Three of the eight senators on a captured search page differ between the
    two, and every filing by those three was silently dropped:

        eFD "A. Mitchell" / "McConnell, Jr."   roster "Mitch"  / "McConnell"
        eFD "Angela D"    / "Alsobrooks"       roster "Angela" / "Alsobrooks"
        eFD "David H"     / "McCormick"        roster "David"  / "McCormick"

    The old query required BOTH a surname equality and a first-name prefix, and
    the prefix was applied to the ROSTER value -- so `first_name ILIKE 'David
    H%'` could never match a roster entry of "David". A surname is near-unique
    within one chamber, so it carries the match and the first name only breaks
    ties.
    """

    def _senator(self, db, first, last, bioguide):
        from src.db.models import Chamber, Member, Party

        member = Member(
            bioguide_id=bioguide,
            first_name=first,
            last_name=last,
            chamber=Chamber.SENATE,
            party=Party.REPUBLICAN,
            state="KY",
        )
        db.add(member)
        db.commit()
        return member

    def _entry(self, first, last, doc_id):
        from datetime import datetime

        return {
            "first_name": first,
            "last_name": last,
            "filing_year": 2025,
            "filing_type": "PTR",
            "filing_date": datetime(2025, 5, 1),
            "document_id": doc_id,
            "document_url": f"https://efdsearch.senate.gov/search/view/ptr/{doc_id}/",
            "chamber": "senate",
            "is_ptr": True,
        }

    def _sync(self, db, tmp_path, roster, entries):
        from src.ingestion.orchestrator import IngestionOrchestrator

        for first, last, bioguide in roster:
            self._senator(db, first, last, bioguide)

        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.senate.search_all_disclosures = lambda year: entries  # type: ignore[method-assign]
        return orch.sync_senate_disclosures(db, 2025)

    def test_a_suffix_on_the_surname_still_matches(self, db_session, tmp_path):
        synced = self._sync(
            db_session,
            tmp_path,
            [("Mitch", "McConnell", "SEN00001")],
            [self._entry("A. Mitchell", "McConnell, Jr.", "SEN-DOC-1")],
        )

        assert synced == 1, "a filing was dropped because eFD appends ', Jr.'"

    def test_a_middle_initial_in_the_first_name_still_matches(self, db_session, tmp_path):
        synced = self._sync(
            db_session,
            tmp_path,
            [("David", "McCormick", "SEN00002")],
            [self._entry("David H", "McCormick", "SEN-DOC-2")],
        )

        assert synced == 1, "a filing was dropped because eFD carries a middle initial"

    def test_case_differences_still_match(self, db_session, tmp_path):
        synced = self._sync(
            db_session,
            tmp_path,
            [("Richard", "Blumenthal", "SEN00003")],
            [self._entry("RICHARD", "BLUMENTHAL", "SEN-DOC-3")],
        )

        assert synced == 1

    def test_two_senators_sharing_a_surname_are_disambiguated(self, db_session, tmp_path):
        synced = self._sync(
            db_session,
            tmp_path,
            [("Ron", "Johnson", "SEN00004"), ("Tim", "Johnson", "SEN00005")],
            [self._entry("Ron", "Johnson", "SEN-DOC-4")],
        )

        assert synced == 1, "a shared surname should be resolved by the first name"

    def test_it_still_refuses_to_guess(self, db_session, tmp_path):
        """The guarantee this must not trade away: attaching a filing to the
        wrong senator is worse than not storing it."""
        synced = self._sync(
            db_session,
            tmp_path,
            [("Ron", "Johnson", "SEN00006"), ("Tim", "Johnson", "SEN00007")],
            [self._entry("", "Johnson", "SEN-DOC-5")],
        )

        assert synced == 0, "an unresolvable filing was attached to a senator anyway"

    def test_an_unknown_senator_is_still_skipped(self, db_session, tmp_path):
        synced = self._sync(
            db_session,
            tmp_path,
            [("Mitch", "McConnell", "SEN00008")],
            [self._entry("Nobody", "Nosuchsenator", "SEN-DOC-6")],
        )

        assert synced == 0


class TestSittingSenatorsWinAgainstAHistoricalRoster:
    """A surname is nowhere near unique once the roster holds all of history.

    Measured against eFD's own filers for 2024-2025: 33 of 105 surnames matched
    more than one senator in the roster -- "Smith" 19, "Scott" 7, "King" 6 --
    and those 33 accounted for 156 filings the ambiguity guard then refused.
    Correctly: it cannot know which Smith. And uselessly: a filing from 2024 is
    from a senator sitting in 2024.

    Restricting to in-office first resolves 102 of the 105 to exactly one
    person, covering 462 of the 490 filings.
    """

    def _senator(self, db, first, last, bioguide, in_office=True):
        from src.db.models import Chamber, Member, Party

        member = Member(
            bioguide_id=bioguide,
            first_name=first,
            last_name=last,
            chamber=Chamber.SENATE,
            party=Party.REPUBLICAN,
            state="FL",
            in_office=in_office,
        )
        db.add(member)
        db.commit()
        return member

    def _entry(self, first, last, doc_id):
        from datetime import datetime

        return {
            "first_name": first,
            "last_name": last,
            "filing_year": 2025,
            "filing_type": "PTR",
            "filing_date": datetime(2025, 5, 1),
            "document_id": doc_id,
            "document_url": f"https://efdsearch.senate.gov/search/view/ptr/{doc_id}/",
            "chamber": "senate",
            "is_ptr": True,
        }

    def _sync(self, db, tmp_path, entries):
        from src.ingestion.orchestrator import IngestionOrchestrator

        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.senate.search_all_disclosures = lambda year: entries  # type: ignore[method-assign]
        return orch.sync_senate_disclosures(db, 2025)

    def test_a_historical_namesake_does_not_block_a_sitting_senator(self, db_session, tmp_path):
        """The real case: 19 Smiths in the roster, one of them in office."""
        for i, first in enumerate(["Benjamin", "Delazon", "Ellison", "Hoke"]):
            self._senator(db_session, first, "Smith", f"HIST{i:04d}", in_office=False)
        self._senator(db_session, "Tina", "Smith", "SITTING01", in_office=True)

        synced = self._sync(db_session, tmp_path, [self._entry("Tina", "Smith", "SMITH-1")])

        assert synced == 1, "a sitting senator lost to four dead namesakes"

    def test_it_works_even_when_the_first_names_share_nothing(self, db_session, tmp_path):
        """'A. Mitchell McConnell, Jr.' against a roster entry of 'Mitch'.

        No prefix and no shared initial, so first-name narrowing cannot save
        this one — the in-office restriction is what does.
        """
        self._senator(db_session, "William", "McConnell", "HISTMC01", in_office=False)
        self._senator(db_session, "Mitch", "McConnell", "SITTMC01", in_office=True)

        synced = self._sync(
            db_session, tmp_path, [self._entry("A. Mitchell", "McConnell, Jr.", "MCC-1")]
        )

        assert synced == 1

    def test_two_sitting_senators_sharing_a_surname_split_on_first_name(self, db_session, tmp_path):
        """Rick and Tim Scott: the one pair the in-office filter cannot separate."""
        self._senator(db_session, "Rick", "Scott", "SCOTT01", in_office=True)
        self._senator(db_session, "Tim", "Scott", "SCOTT02", in_office=True)

        assert self._sync(db_session, tmp_path, [self._entry("Rick", "Scott", "SC-1")]) == 1
        assert self._sync(db_session, tmp_path, [self._entry("Tim", "Scott", "SC-2")]) == 1

    def test_a_senator_who_has_left_office_still_matches(self, db_session, tmp_path):
        """The fallback. Filings persist after a term ends, and restricting to
        sitting senators must not become a new way to lose them."""
        self._senator(db_session, "Former", "Retiree", "GONE0001", in_office=False)

        synced = self._sync(db_session, tmp_path, [self._entry("Former", "Retiree", "RET-1")])

        assert synced == 1

    def test_an_accent_in_the_roster_still_matches(self, db_session, tmp_path):
        """eFD shouts ASCII: 'BEN RAY' 'LUJAN'. The roster carries 'Lujan' with
        an acute accent. This was the one filer of 105 whose surname matched
        nothing at all."""
        self._senator(db_session, "Ben Ray", "Luján", "LUJAN001", in_office=True)

        synced = self._sync(db_session, tmp_path, [self._entry("BEN RAY", "LUJAN", "LUJ-1")])

        assert synced == 1, "an accented surname was unmatchable from eFD's ASCII"

    def test_it_still_refuses_when_it_genuinely_cannot_tell(self, db_session, tmp_path):
        """Unchanged, and the point of the whole design: two sitting senators,
        no usable first name, so the filing is not stored rather than pinned on
        the wrong person."""
        self._senator(db_session, "Rick", "Scott", "SCOTT03", in_office=True)
        self._senator(db_session, "Tim", "Scott", "SCOTT04", in_office=True)

        synced = self._sync(db_session, tmp_path, [self._entry("", "Scott", "SC-3")])

        assert synced == 0

    def test_the_roster_is_read_once_not_per_filing(self, db_session, tmp_path):
        """A query per filing is how the leaderboards got slow."""
        from sqlalchemy import event

        from src.db import engine

        self._senator(db_session, "Solo", "Senator", "SOLO0001", in_office=True)
        entries = [self._entry("Solo", "Senator", f"MANY-{i}") for i in range(25)]

        seen: list[str] = []

        def record(conn, cursor, statement, params, context, executemany):
            if "FROM members" in statement:
                seen.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            self._sync(db_session, tmp_path, entries)
        finally:
            event.remove(engine, "before_cursor_execute", record)

        assert len(seen) <= 3, (
            f"{len(seen)} member queries for 25 filings — the roster is being re-read per row"
        )
