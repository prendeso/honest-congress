"""Targeted tests for the ingestion orchestrator.

The full orchestrator is large (~900 lines) and most methods make outbound
HTTP calls. These tests pin behavior on the self-contained pieces — DB
maintenance and URL building — so a future decomposition has guardrails.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.db.models import Chamber, Disclosure, Member, Party
from src.ingestion.orchestrator import IngestionOrchestrator


def _make_member(db) -> Member:
    m = Member(
        bioguide_id="O000001",
        first_name="Orch",
        last_name="Test",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


class TestFixFutureDates:
    def test_no_future_dates_returns_zero(self, db_session):
        member = _make_member(db_session)
        db_session.add(
            Disclosure(
                member_id=member.id,
                filing_year=2024,
                filing_type="FD",
                filing_date=datetime(2024, 6, 1),
                document_id="DOC1",
                parsed=True,
            )
        )
        db_session.commit()

        assert IngestionOrchestrator().fix_future_dates(db_session) == 0

    def test_future_dates_clamped_to_now(self, db_session):
        member = _make_member(db_session)
        future = datetime.now() + timedelta(days=365)
        d = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="FD",
            filing_date=future,
            document_id="FUTURE_DOC",
            parsed=True,
        )
        db_session.add(d)
        db_session.commit()

        fixed = IngestionOrchestrator().fix_future_dates(db_session)
        assert fixed == 1

        db_session.refresh(d)
        # Now-clamped, so it should no longer be in the future.
        assert d.filing_date <= datetime.now()

    def test_only_fixes_future_disclosures(self, db_session):
        """Past-dated disclosures must not be touched."""
        member = _make_member(db_session)
        past = datetime(2020, 1, 1)
        future = datetime.now() + timedelta(days=10)

        db_session.add_all(
            [
                Disclosure(
                    member_id=member.id,
                    filing_year=2020,
                    filing_type="FD",
                    filing_date=past,
                    document_id="PAST_DOC",
                    parsed=True,
                ),
                Disclosure(
                    member_id=member.id,
                    filing_year=2024,
                    filing_type="FD",
                    filing_date=future,
                    document_id="FUTURE_DOC",
                    parsed=True,
                ),
            ]
        )
        db_session.commit()

        assert IngestionOrchestrator().fix_future_dates(db_session) == 1
        # Re-running yields zero (idempotent).
        assert IngestionOrchestrator().fix_future_dates(db_session) == 0

        past_d = db_session.query(Disclosure).filter_by(document_id="PAST_DOC").one()
        assert past_d.filing_date == past  # unchanged


class TestSyncSenateDisclosures:
    """The returned count must equal rows actually written.

    This method used to build a Disclosure, leave `db.add` commented out, and
    increment `synced` anyway -- so `run_full_sync` reported Senate filings that
    were never stored, and the CLI printed that number to the operator.
    """

    @staticmethod
    def _senator(db, first="Jane", last="Doe", bioguide="S000001") -> Member:
        m = Member(
            bioguide_id=bioguide,
            first_name=first,
            last_name=last,
            chamber=Chamber.SENATE,
            party=Party.DEMOCRAT,
            state="NY",
        )
        db.add(m)
        db.commit()
        db.refresh(m)
        return m

    @staticmethod
    def _row(doc_id="SEN-1", first="Jane", last="Doe") -> dict:
        return {
            "document_id": doc_id,
            "document_url": f"https://efdsearch.senate.gov/{doc_id}",
            "filing_year": 2024,
            "filing_type": "Annual Report",
            "filing_date": datetime(2024, 5, 15),
            "first_name": first,
            "last_name": last,
        }

    def _orchestrator(self, rows):
        orch = IngestionOrchestrator()
        orch.senate.search_all_disclosures = lambda year: rows  # type: ignore[method-assign]
        return orch

    def test_count_matches_rows_actually_persisted(self, db_session):
        self._senator(db_session)
        orch = self._orchestrator([self._row()])

        synced = orch.sync_senate_disclosures(db_session, year=2024)

        stored = db_session.query(Disclosure).filter(Disclosure.document_id == "SEN-1").count()
        assert synced == 1
        assert stored == synced, "reported count must equal rows written"

    def test_unmatched_member_is_not_counted(self, db_session):
        # No senator seeded -> nothing can be linked, so nothing is stored.
        orch = self._orchestrator([self._row(first="Nobody", last="Missing")])

        synced = orch.sync_senate_disclosures(db_session, year=2024)

        assert synced == 0
        assert db_session.query(Disclosure).count() == 0

    def test_ambiguous_match_is_skipped_not_misattributed(self, db_session):
        self._senator(db_session, first="Jane", last="Doe", bioguide="S000001")
        self._senator(db_session, first="Janet", last="Doe", bioguide="S000002")
        orch = self._orchestrator([self._row(first="Jan", last="Doe")])

        synced = orch.sync_senate_disclosures(db_session, year=2024)

        assert synced == 0
        assert db_session.query(Disclosure).count() == 0

    def test_existing_document_id_is_not_double_counted(self, db_session):
        senator = self._senator(db_session)
        db_session.add(
            Disclosure(
                member_id=senator.id,
                filing_year=2024,
                filing_type="Annual Report",
                filing_date=datetime(2024, 5, 15),
                document_id="SEN-1",
                document_url="https://efdsearch.senate.gov/SEN-1",
                parsed=False,
            )
        )
        db_session.commit()
        orch = self._orchestrator([self._row()])

        synced = orch.sync_senate_disclosures(db_session, year=2024)

        assert synced == 0
        assert db_session.query(Disclosure).filter(Disclosure.document_id == "SEN-1").count() == 1


class TestScannedFilingsAreRecordedAsSuch:
    """`has_text_layer` is written on every parse, and re-parses skip the scans.

    12.7% of 2024-25 House PTRs are scans with no extractable text. They score
    0.0 and always will. Without the flag, every `--min-confidence` re-parse run
    downloads and re-reads one filing in eight to arrive at the same answer, and
    the failure count reported to a reader blames the parser for the form.
    """

    def _orchestrator(self, tmp_path, *, text_extracted: bool):
        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.download_disclosure_pdf = lambda d: tmp_path / "x.pdf"  # type: ignore[method-assign]
        (tmp_path / "x.pdf").write_bytes(b"%PDF-1.4")
        orch.ptr_parser.parse_ptr = lambda path: {  # type: ignore[method-assign]
            "quality": {
                "text_extracted": text_extracted,
                "rows_detected": 1 if text_extracted else 0,
                "rows_parsed": 1 if text_extracted else 0,
            },
            "transactions": (
                [
                    {
                        "transaction_date": datetime(2024, 3, 1),
                        "transaction_type": "purchase",
                        "amount_min": 1001,
                        "amount_max": 15000,
                        "description": "AAPL",
                        "ticker": "AAPL",
                    }
                ]
                if text_extracted
                else []
            ),
        }
        return orch

    def _ptr(self, db, member, doc_id):
        d = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="PTR",
            filing_date=datetime(2024, 5, 1),
            document_id=doc_id,
            is_ptr=True,
        )
        db.add(d)
        db.commit()
        db.refresh(d)
        return d

    def test_a_scan_is_flagged_and_a_readable_filing_is_not(self, db_session, tmp_path):
        member = _make_member(db_session)

        scan = self._ptr(db_session, member, "SCAN-9")
        self._orchestrator(tmp_path, text_extracted=False).parse_disclosure(db_session, scan)
        assert scan.has_text_layer is False
        assert scan.parse_confidence == 0.0

        readable = self._ptr(db_session, member, "TEXT-9")
        self._orchestrator(tmp_path, text_extracted=True).parse_disclosure(db_session, readable)
        assert readable.has_text_layer is True
        assert readable.parse_confidence and readable.parse_confidence > 0

    def test_a_limited_reparse_advances_instead_of_repeating(self, db_session, tmp_path):
        """`--min-confidence` with `--limit` must reach the whole corpus.

        The query had no ORDER BY, so the database returned an arbitrary set.
        A filing that re-parses to less than the threshold still matches the
        filter, so a limited run took the same rows every time and the rest of
        the corpus was never reached, however many times the job was
        dispatched. Ordering by `updated_at` -- which `onupdate` bumps on every
        parse -- sends a filing to the back of the queue as soon as it is read.
        """
        member = _make_member(db_session)
        for index in range(4):
            filing = self._ptr(db_session, member, f"LOW-{index}")
            filing.parsed, filing.parse_confidence, filing.has_text_layer = True, 0.5, True
            filing.updated_at = datetime(2024, 1, 1 + index)
        db_session.commit()

        # Parse two, then two more. Every filing still scores below 1.0
        # afterwards, so nothing leaves the filter -- only the ordering can
        # make the second run pick up different rows.
        orch = self._orchestrator(tmp_path, text_extracted=True)
        orch.ptr_parser.parse_ptr = lambda path: {  # type: ignore[method-assign]
            "quality": {"text_extracted": True, "rows_detected": 2, "rows_parsed": 1},
            "transactions": [
                {
                    "transaction_date": datetime(2024, 3, 1),
                    "transaction_type": "purchase",
                    "amount_min": 1001,
                    "amount_max": 15000,
                    "description": "AAPL",
                    "ticker": "AAPL",
                }
            ],
        }

        first = {d.document_id for d in self._reparse_batch(db_session, orch, limit=2)}
        second = {d.document_id for d in self._reparse_batch(db_session, orch, limit=2)}

        assert first == {"LOW-0", "LOW-1"}, "oldest first"
        assert second == {"LOW-2", "LOW-3"}, (
            f"the second run repeated {first & second} instead of advancing"
        )

    def _reparse_batch(self, db, orch, *, limit):
        """Run one limited re-parse and report which filings it touched."""
        before = {d.document_id: d.updated_at for d in db.query(Disclosure).all()}
        orch.parse_disclosures(db, min_confidence=1.0, limit=limit, delay=0)
        db.expire_all()
        return [d for d in db.query(Disclosure).all() if d.updated_at != before.get(d.document_id)]

    def test_reparsing_the_low_scorers_leaves_the_scans_alone(self, db_session, tmp_path):
        """A scan scores 0.0 forever. Re-reading it every night buys nothing."""
        member = _make_member(db_session)

        scan = self._ptr(db_session, member, "SCAN-8")
        scan.parsed, scan.parse_confidence, scan.has_text_layer = True, 0.0, False
        broken = self._ptr(db_session, member, "BROKEN-8")
        broken.parsed, broken.parse_confidence, broken.has_text_layer = True, 0.0, True
        never_checked = self._ptr(db_session, member, "OLD-8")
        never_checked.parsed = True
        db_session.commit()

        orch = self._orchestrator(tmp_path, text_extracted=True)
        result = orch.parse_disclosures(db_session, min_confidence=0.8, delay=0)

        assert result["parsed"] == 2, "the broken filing and the never-checked one, not the scan"
        assert scan.parse_confidence == 0.0
        assert scan.has_text_layer is False


class TestReparsingReplacesRatherThanAppends:
    """Re-reading a filing must not add a second copy of what it already held.

    `_store_ptr_data` and `_store_fd_data` only ever `db.add(...)`, and
    transactions/assets/liabilities carry no uniqueness constraint -- only
    non-unique indexes. So every re-parse appended: one disclosed trade became
    1, then 2, then 3 rows across three calls.

    Nothing caught it because the default filter only selects filings never
    parsed, which have nothing to duplicate. Both re-read paths hit it: the
    long-standing `--reparse`, and `--min-confidence`, whose entire purpose is
    to re-read the corpus after a parser fix. Pointed at a populated database
    that would have doubled the transaction table -- and a duplicate is
    indistinguishable from a member genuinely reporting the same ticker, band,
    type and date twice, so it could not have been cleaned up afterwards.
    """

    TXN = {
        "transaction_date": datetime(2024, 3, 1),
        "transaction_type": "purchase",
        "amount_min": 1001,
        "amount_max": 15000,
        "description": "Apple Inc",
        "ticker": "AAPL",
    }

    def _orchestrator(self, tmp_path, transactions):
        orch = IngestionOrchestrator(data_dir=tmp_path)
        (tmp_path / "x.pdf").write_bytes(b"%PDF-1.4")
        orch.download_disclosure_pdf = lambda d: tmp_path / "x.pdf"  # type: ignore[method-assign]
        orch.ptr_parser.parse_ptr = lambda path: {  # type: ignore[method-assign]
            "quality": {
                "text_extracted": True,
                "rows_detected": len(transactions),
                "rows_parsed": len(transactions),
            },
            "transactions": list(transactions),
        }
        return orch

    def _ptr(self, db, member, doc_id):
        d = Disclosure(
            member_id=member.id,
            filing_year=2024,
            filing_type="PTR",
            filing_date=datetime(2024, 5, 1),
            document_id=doc_id,
            is_ptr=True,
        )
        db.add(d)
        db.commit()
        db.refresh(d)
        return d

    def _count(self, db, disclosure):
        from src.db.models import Transaction

        return db.query(Transaction).filter(Transaction.disclosure_id == disclosure.id).count()

    def test_three_reparses_leave_one_row(self, db_session, tmp_path):
        member = _make_member(db_session)
        filing = self._ptr(db_session, member, "REPARSE-1")
        orch = self._orchestrator(tmp_path, [self.TXN])

        counts = []
        for _ in range(3):
            orch.parse_disclosure(db_session, filing)
            db_session.expire_all()
            counts.append(self._count(db_session, filing))

        assert counts == [1, 1, 1], f"re-parse duplicated rows: {counts}"

    def test_a_changed_parse_replaces_the_old_rows(self, db_session, tmp_path):
        """The point of re-parsing is that the new reading wins."""
        member = _make_member(db_session)
        filing = self._ptr(db_session, member, "REPARSE-2")

        wrong = {**self.TXN, "transaction_type": "purchase", "description": "Best Co., Inc."}
        self._orchestrator(tmp_path, [wrong]).parse_disclosure(db_session, filing)

        right = {**self.TXN, "transaction_type": "sale", "description": "Best Buy Co., Inc."}
        self._orchestrator(tmp_path, [right]).parse_disclosure(db_session, filing)
        db_session.expire_all()

        from src.db.models import Transaction, TransactionType

        rows = db_session.query(Transaction).filter(Transaction.disclosure_id == filing.id).all()
        assert len(rows) == 1
        assert rows[0].transaction_type == TransactionType.SALE
        assert rows[0].description == "Best Buy Co., Inc."

    def test_a_reparse_that_finds_nothing_keeps_the_good_rows(self, db_session, tmp_path):
        """The other half of the fix, and the more dangerous direction.

        Clearing unconditionally would mean a failed download, a scan, or a
        layout the parser lost silently deletes transactions an earlier parse
        got right. Replacing is only safe when there is something to replace
        them with.
        """
        member = _make_member(db_session)
        filing = self._ptr(db_session, member, "REPARSE-3")
        self._orchestrator(tmp_path, [self.TXN]).parse_disclosure(db_session, filing)
        db_session.expire_all()
        assert self._count(db_session, filing) == 1

        self._orchestrator(tmp_path, []).parse_disclosure(db_session, filing)
        db_session.expire_all()

        assert self._count(db_session, filing) == 1, (
            "a re-parse that yielded nothing deleted rows a previous parse got right"
        )
        assert filing.parse_confidence == 0.0, "and it must still be scored as having read nothing"


class TestADuplicateDocIdDoesNotKillTheIngest:
    """The House Clerk publishes the same DocID more than once.

    Not hypothetical: the 2025 annual index contains four duplicated DocIDs,
    and one of them ended a production ingest three minutes in with

        UniqueViolation: duplicate key value violates unique constraint
        "disclosures_document_id_key"
        DETAIL:  Key (document_id)=(10078188) already exists.

    The existence check queried the database, but `SessionLocal` is built with
    `autoflush=False`, so a row added earlier in the same loop is invisible to
    it. Both copies passed the check, both were added, and the single
    `db.commit()` after the loop failed -- taking the whole year with it,
    because that commit sits outside the per-item `try`.
    """

    def _index_entry(self, member, doc_id):
        return {
            "first_name": member.first_name,
            "last_name": member.last_name,
            "full_name": f"{member.first_name} {member.last_name}",
            "state": member.state,
            "district": "01",
            "filing_type": "O",
            "filing_date": datetime(2025, 5, 1),
            "filing_year": 2025,
            "document_id": doc_id,
            "document_url": f"https://example.invalid/{doc_id}.pdf",
            "chamber": "house",
        }

    def test_the_same_docid_twice_in_one_index_is_stored_once(self, db_session, tmp_path):
        member = _make_member(db_session)
        orch = IngestionOrchestrator(data_dir=tmp_path)

        # 10078188 is a real duplicate in the Clerk's 2025 index.
        entries = [
            self._index_entry(member, "10078188"),
            self._index_entry(member, "10078188"),
            self._index_entry(member, "10078189"),
        ]
        orch.house.fetch_annual_xml_index = lambda year: entries  # type: ignore[method-assign]

        synced = orch.sync_house_disclosures(db_session, year=2025)

        stored = db_session.query(Disclosure).filter(Disclosure.filing_year == 2025).all()
        assert len(stored) == 2, f"expected 2 distinct filings, stored {len(stored)}"
        assert {d.document_id for d in stored} == {"10078188", "10078189"}
        assert synced == 2

    def test_the_run_survives_and_stores_everything_after_the_duplicate(self, db_session, tmp_path):
        """The damage was not the duplicate -- it was losing the whole year.

        The commit that raised sat outside the per-item `try`, so one repeated
        DocID discarded every filing queued behind it.
        """
        member = _make_member(db_session)
        orch = IngestionOrchestrator(data_dir=tmp_path)

        entries = [self._index_entry(member, "10078188")]
        entries += [self._index_entry(member, "10078188")]
        entries += [self._index_entry(member, f"2000{i:04d}") for i in range(25)]
        orch.house.fetch_annual_xml_index = lambda year: entries  # type: ignore[method-assign]

        orch.sync_house_disclosures(db_session, year=2025)

        stored = db_session.query(Disclosure).filter(Disclosure.filing_year == 2025).count()
        assert stored == 26, f"the filings after the duplicate were lost: {stored} of 26"

    def test_re_running_the_ingest_adds_nothing(self, db_session, tmp_path):
        """Idempotence across runs, which is what makes re-dispatch safe."""
        member = _make_member(db_session)
        orch = IngestionOrchestrator(data_dir=tmp_path)
        entries = [self._index_entry(member, f"3000{i:04d}") for i in range(5)]
        orch.house.fetch_annual_xml_index = lambda year: entries  # type: ignore[method-assign]

        orch.sync_house_disclosures(db_session, year=2025)
        first = db_session.query(Disclosure).count()
        orch.sync_house_disclosures(db_session, year=2025)
        second = db_session.query(Disclosure).count()

        assert first == second == 5


class TestOneUnstorableFilingDoesNotKillTheRun:
    """A filing the database refuses must cost one failure, not the queue.

    A 2024 House filing extracted with NUL bytes in an asset description --
    a font with no usable ToUnicode map, so every glyph came back U+0000.
    PostgreSQL rejects NUL in a text field with a `psycopg.DataError` raised
    at flush time, which leaves the session needing a rollback.

    Neither handler issued one, and both then touched the session: the inner
    one read `disclosure.document_id` (expired by the previous commit, so the
    read goes to the database) and called `db.commit()`. So the error handler
    raised PendingRollbackError from inside itself, that escaped the loop, and
    a parse run of a thousand filings ended nine seconds in having stored
    nothing. The second filing of three below is the one the database refuses;
    the third is what the old code never reached.
    """

    def _orchestrator(self, tmp_path, member, poison_doc_id):
        orch = IngestionOrchestrator(data_dir=tmp_path)
        (tmp_path / "x.pdf").write_bytes(b"%PDF-1.4")
        orch.download_disclosure_pdf = lambda d: tmp_path / "x.pdf"  # type: ignore[method-assign]

        # The failure has to come from the FLUSH, not from any old statement.
        # Only a rejected flush sets the session's rollback-required flag, and
        # that flag is what turns every later ORM call into PendingRollbackError
        # -- the whole mechanism under test. A statement error alone leaves the
        # session usable and reproduces nothing. So the stand-in queues a row
        # the database will refuse (a duplicate `document_id`, which carries a
        # unique constraint) and lets `parse_disclosure`'s own `db.commit()`
        # hit it, exactly as the NUL byte did on PostgreSQL.
        state = {"session": None, "doc": None}

        def fake_parse_ptr(path):
            db = state["session"]
            if state["doc"] == poison_doc_id:
                db.add(
                    Disclosure(
                        member_id=member.id,
                        filing_year=2024,
                        filing_type="PTR",
                        filing_date=datetime(2024, 5, 1),
                        document_id="NUL-1",
                        is_ptr=True,
                    )
                )
            return {
                "quality": {"text_extracted": True, "rows_detected": 1, "rows_parsed": 1},
                "transactions": [
                    {
                        "transaction_date": datetime(2024, 3, 1),
                        "transaction_type": "purchase",
                        "amount_min": 1001,
                        "amount_max": 15000,
                        "description": "Apple Inc",
                        "ticker": "AAPL",
                    }
                ],
            }

        orch.ptr_parser.parse_ptr = fake_parse_ptr  # type: ignore[method-assign]

        original = orch.parse_disclosure

        def tracking_parse(db, disclosure, pdf_path=None):
            state["session"] = db
            state["doc"] = disclosure.document_id
            return original(db, disclosure, pdf_path)

        orch.parse_disclosure = tracking_parse  # type: ignore[method-assign]
        return orch

    def test_the_run_continues_past_the_filing_the_database_refuses(self, db_session, tmp_path):
        member = _make_member(db_session)
        for doc_id in ("NUL-1", "NUL-2", "NUL-3"):
            db_session.add(
                Disclosure(
                    member_id=member.id,
                    filing_year=2024,
                    filing_type="PTR",
                    filing_date=datetime(2024, 5, 1),
                    document_id=doc_id,
                    is_ptr=True,
                )
            )
        db_session.commit()

        orch = self._orchestrator(tmp_path, member, poison_doc_id="NUL-2")

        # The assertion is first of all that this returns at all. Before the
        # fix it raised PendingRollbackError out of the loop.
        results = orch.parse_disclosures(db_session, limit=10)

        assert results["failed"] == 1, results
        assert results["parsed"] == 2, (
            f"the run stopped at the bad filing instead of continuing past it: {results}"
        )

        db_session.expire_all()
        parsed = {
            d.document_id: d.parsed
            for d in db_session.query(Disclosure)
            .filter(Disclosure.document_id.in_(["NUL-1", "NUL-2", "NUL-3"]))
            .all()
        }
        assert parsed["NUL-1"] is True
        assert parsed["NUL-3"] is True, "the filing queued after the bad one was never read"


class TestFilingsWithNoMatchingMemberAreCounted:
    """A filing whose filer matches no member is dropped. Silently, before this.

    The lookup wants an exact last name, a first-name prefix, the chamber and
    the state. On a miss the loop did `continue` behind a `logger.debug`, so
    nothing counted it and nothing reported it -- and two very different things
    looked identical from outside a run:

      * a candidate report (FilingType "C") from somebody who is not in
        Congress, correctly skipped
      * an annual filing or a PTR from a sitting member whose name we failed to
        match, which is a filing missing from the site

    The House index for 2024 and 2025 holds 5,219 entries, 1,570 of them
    candidate reports. Whether the remainder all landed was not answerable from
    a run's output. These tests hold the counting open; they assert nothing
    about which filings ought to match, which is a separate question that needs
    these numbers first.
    """

    def _entry(self, last_name, filing_type="O", state="CA", doc_id="UM-1"):
        return {
            "first_name": "Nomatch",
            "last_name": last_name,
            "full_name": f"Nomatch {last_name}",
            "state": state,
            "district": "01",
            "filing_type": filing_type,
            "filing_date": datetime(2025, 5, 1),
            "filing_year": 2025,
            "document_id": doc_id,
            "document_url": "https://example.invalid/x.pdf",
            "chamber": "house",
        }

    def test_an_unmatched_annual_filing_is_counted_as_unexpected(self, db_session, tmp_path):
        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.house.fetch_annual_xml_index = lambda year: [  # type: ignore[method-assign]
            self._entry("Ghost", filing_type="O", doc_id="UM-A")
        ]

        synced = orch.sync_house_disclosures(db_session, 2025)

        assert synced == 0
        assert orch.unmatched_filers == 1, "the dropped filing was not counted"

    def test_a_candidate_report_is_counted_but_not_flagged(self, db_session, tmp_path):
        """Candidates are not members. Missing them is the system working, and
        reporting them as a problem would bury the real ones."""
        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.house.fetch_annual_xml_index = lambda year: [  # type: ignore[method-assign]
            self._entry("Hopeful", filing_type="C", doc_id="UM-C")
        ]

        orch.sync_house_disclosures(db_session, 2025)

        assert orch.unmatched_filers == 0, "a candidate report was reported as a lost filing"

    def test_an_unmatched_ptr_always_counts(self, db_session, tmp_path):
        """Every PTR in the index is a disclosed trade. There is no benign
        class of miss here."""
        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.house.fetch_ptr_xml_index = lambda year: [  # type: ignore[method-assign]
            {**self._entry("Ghost", filing_type="PTR", doc_id="UM-P"), "is_ptr": True}
        ]

        orch.sync_house_ptrs(db_session, 2025)

        assert orch.unmatched_filers == 1

    def test_a_filing_that_does_match_is_not_counted(self, db_session, tmp_path):
        member = _make_member(db_session)
        orch = IngestionOrchestrator(data_dir=tmp_path)
        entry = self._entry(member.last_name, doc_id="UM-OK")
        entry["first_name"] = member.first_name
        entry["state"] = member.state
        orch.house.fetch_annual_xml_index = lambda year: [entry]  # type: ignore[method-assign]

        synced = orch.sync_house_disclosures(db_session, 2025)

        assert synced == 1
        assert orch.unmatched_filers == 0

    def test_the_breakdown_names_the_filing_types(self, db_session, tmp_path, caplog):
        """The count alone does not say whether it matters. The breakdown does."""
        import logging

        orch = IngestionOrchestrator(data_dir=tmp_path)
        orch.house.fetch_annual_xml_index = lambda year: [  # type: ignore[method-assign]
            self._entry("Hopeful", filing_type="C", doc_id="UM-C2"),
            self._entry("Ghost", filing_type="O", doc_id="UM-O2"),
        ]

        with caplog.at_level(logging.WARNING, logger="src.ingestion.orchestrator"):
            orch.sync_house_disclosures(db_session, 2025)

        message = "\n".join(r.getMessage() for r in caplog.records)
        assert "C=1" in message and "O=1" in message, message
        assert orch.unmatched_filers == 1
