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
