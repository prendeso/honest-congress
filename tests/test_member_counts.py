"""Tests for the materialized member count columns.

`Member.disclosure_count` and `Member.anomaly_count` are denormalized, and
`src/api/routes/members.py` filters, sorts and returns them. Nothing ever
maintained them -- `src/db/utils.py` had zero callers -- so they sat at zero
and `GET /api/members?sort_by=anomalies` returned meaningless ordering.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.db.models import Anomaly, Chamber, Disclosure, Member, Party
from src.db.utils import recalculate_member_counts


def _member(db, bioguide: str, last: str) -> Member:
    m = Member(
        bioguide_id=bioguide,
        first_name="Count",
        last_name=last,
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _disclosure(db, member: Member, doc_id: str) -> Disclosure:
    d = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="FD",
        filing_date=datetime(2024, 6, 1),
        document_id=doc_id,
        document_url=f"https://example.invalid/{doc_id}",
        parsed=False,
    )
    db.add(d)
    db.commit()
    return d


def _anomaly(db, member: Member, title: str) -> Anomaly:
    a = Anomaly(
        member_id=member.id,
        anomaly_type="large_trade",
        severity="high",
        title=title,
        description="test",
    )
    db.add(a)
    db.commit()
    return a


class TestRecalculateMemberCounts:
    def test_counts_reflect_rows(self, db_session):
        m = _member(db_session, "C000001", "Alpha")
        _disclosure(db_session, m, "C-DOC-1")
        _disclosure(db_session, m, "C-DOC-2")
        _anomaly(db_session, m, "first")

        recalculate_member_counts(db_session)
        db_session.refresh(m)

        assert m.disclosure_count == 2
        assert m.anomaly_count == 1

    def test_member_with_no_rows_is_zeroed(self, db_session):
        m = _member(db_session, "C000002", "Beta")
        # Seed a stale non-zero value, as an un-maintained column would carry.
        m.disclosure_count = 99
        m.anomaly_count = 99
        db_session.commit()

        recalculate_member_counts(db_session)
        db_session.refresh(m)

        assert m.disclosure_count == 0
        assert m.anomaly_count == 0

    def test_counts_drop_after_rows_are_deleted(self, db_session):
        m = _member(db_session, "C000003", "Gamma")
        a = _anomaly(db_session, m, "to be deleted")
        recalculate_member_counts(db_session)
        db_session.refresh(m)
        assert m.anomaly_count == 1

        db_session.delete(a)
        db_session.commit()
        recalculate_member_counts(db_session)
        db_session.refresh(m)

        assert m.anomaly_count == 0

    def test_counts_are_per_member_not_global(self, db_session):
        first = _member(db_session, "C000004", "Delta")
        second = _member(db_session, "C000005", "Epsilon")
        _disclosure(db_session, first, "C-DOC-3")
        _disclosure(db_session, first, "C-DOC-4")
        _disclosure(db_session, second, "C-DOC-5")

        recalculate_member_counts(db_session)
        db_session.refresh(first)
        db_session.refresh(second)

        assert first.disclosure_count == 2
        assert second.disclosure_count == 1

    def test_is_idempotent(self, db_session):
        m = _member(db_session, "C000006", "Zeta")
        _disclosure(db_session, m, "C-DOC-6")

        recalculate_member_counts(db_session)
        recalculate_member_counts(db_session)
        db_session.refresh(m)

        assert m.disclosure_count == 1

    def test_returns_totals(self, db_session):
        m = _member(db_session, "C000007", "Eta")
        _disclosure(db_session, m, "C-DOC-7")
        _anomaly(db_session, m, "counted")

        stats = recalculate_member_counts(db_session)

        assert stats["members"] >= 1
        assert stats["disclosures"] >= 1
        assert stats["anomalies"] >= 1


class TestMembersApiSortingUsesLiveCounts:
    """`/api/members?sort_by=anomalies` must order by real counts.

    members.py filters, sorts and returns Member.anomaly_count, but nothing
    maintained the column, so the ordering was meaningless in production.
    """

    @pytest.fixture
    def api(self):
        from fastapi.testclient import TestClient

        from src.api.main import app
        from src.db import SessionLocal
        from src.db.models import Asset, Transaction

        db = SessionLocal()
        for table in (Anomaly, Transaction, Asset, Disclosure, Member):
            db.query(table).delete()
        db.commit()

        # Seed three members with 0, 1 and 3 anomalies respectively, and leave
        # every anomaly_count at its default so the recompute has real work.
        for idx, (bioguide, count) in enumerate([("S000010", 0), ("S000011", 1), ("S000012", 3)]):
            m = Member(
                bioguide_id=bioguide,
                first_name="Sort",
                last_name=f"Case{idx}",
                chamber=Chamber.HOUSE,
                party=Party.DEMOCRAT,
                state="CA",
                in_office=True,
            )
            db.add(m)
            db.commit()
            db.refresh(m)
            for n in range(count):
                db.add(
                    Anomaly(
                        member_id=m.id,
                        anomaly_type="large_trade",
                        severity="high",
                        title=f"{bioguide} finding {n}",
                        description="sorting fixture",
                    )
                )
            db.commit()

        recalculate_member_counts(db)
        yield TestClient(app)
        db.close()

    def test_sort_by_anomalies_desc_puts_the_busiest_member_first(self, api):
        r = api.get("/api/members?sort_by=anomalies&sort_order=desc")
        assert r.status_code == 200

        counts = [m["anomaly_count"] for m in r.json()["members"]]
        assert counts == sorted(counts, reverse=True)
        assert counts[0] == 3

    def test_min_anomalies_filter_uses_real_counts(self, api):
        r = api.get("/api/members?min_anomalies=2")
        assert r.status_code == 200

        body = r.json()
        assert body["total"] == 1
        assert body["members"][0]["anomaly_count"] == 3
