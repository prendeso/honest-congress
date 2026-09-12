"""Smoke + behavior tests for the JSON API endpoints.

These tests use FastAPI's TestClient against an in-memory SQLite DB so
they're fast and don't need network access. They cover the contract that
matters for downstream consumers: every endpoint returns the expected
shape on an empty database, applies filters correctly, and surfaces
proper HTTP status codes for error paths.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.db import SessionLocal
from src.db.models import (
    Anomaly,
    Asset,
    AssetType,
    Chamber,
    Disclosure,
    Member,
    Party,
    Transaction,
    TransactionType,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def seeded_db():
    """Seed a couple of representative rows before each test."""
    db = SessionLocal()
    # Wipe + reseed to keep tests deterministic.
    for table in (Anomaly, Transaction, Asset, Disclosure, Member):
        db.query(table).delete()
    db.commit()

    member = Member(
        bioguide_id="A000001",
        first_name="Alice",
        last_name="Example",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
        in_office=True,
        anomaly_count=1,
        disclosure_count=1,
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    disclosure = Disclosure(
        member_id=member.id,
        filing_year=2024,
        filing_type="PTR",
        filing_date=datetime(2024, 6, 1),
        document_id="DOC1",
        is_ptr=True,
        parsed=True,
    )
    db.add(disclosure)
    db.commit()
    db.refresh(disclosure)

    db.add(
        Transaction(
            disclosure_id=disclosure.id,
            transaction_date=datetime(2024, 3, 15),
            transaction_type=TransactionType.PURCHASE,
            description="AAPL purchase",
            ticker="AAPL",
            amount_min=Decimal("1000"),
            amount_max=Decimal("15000"),
        )
    )
    db.add(
        Asset(
            disclosure_id=disclosure.id,
            asset_type=AssetType.STOCK,
            description="Apple stock",
            value_min=Decimal("1000"),
            value_max=Decimal("15000"),
        )
    )
    db.add(
        Anomaly(
            member_id=member.id,
            anomaly_type="large_trade",
            severity="high",
            title="Test anomaly",
            description="Test description",
        )
    )
    db.commit()
    yield db
    db.close()


# ---------------- health ----------------


class TestHealth:
    def test_health_returns_200_when_db_reachable(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "healthy", "database": "connected"}

    def test_health_live_always_200(self, client):
        r = client.get("/health/live")
        assert r.status_code == 200
        assert r.json() == {"status": "alive"}

    def test_request_id_echoed_in_response_header(self, client):
        r = client.get("/health/live", headers={"X-Request-ID": "abc-123"})
        assert r.headers.get("x-request-id") == "abc-123"

    def test_request_id_generated_when_absent(self, client):
        r = client.get("/health/live")
        assert r.headers.get("x-request-id"), "expected an auto-generated request ID"


# ---------------- members ----------------


class TestMembersAPI:
    def test_list_returns_pagination_envelope(self, client, seeded_db):
        r = client.get("/api/members")
        assert r.status_code == 200
        body = r.json()
        assert {"total", "page", "page_size", "members"} <= body.keys()
        assert body["total"] == 1
        assert body["members"][0]["last_name"] == "Example"

    def test_list_filters_by_party(self, client, seeded_db):
        r = client.get("/api/members?party=R")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_list_filters_by_state(self, client, seeded_db):
        r = client.get("/api/members?state=CA")
        assert r.json()["total"] == 1
        r = client.get("/api/members?state=NY")
        assert r.json()["total"] == 0

    def test_get_member_by_id(self, client, seeded_db):
        member_id = seeded_db.query(Member).first().id
        r = client.get(f"/api/members/{member_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["bioguide_id"] == "A000001"
        assert "recent_disclosures" in body

    def test_get_member_404_when_missing(self, client, seeded_db):
        r = client.get("/api/members/999999")
        assert r.status_code == 404


# ---------------- anomalies ----------------


class TestAnomaliesAPI:
    def test_list_returns_seeded_anomaly(self, client, seeded_db):
        # / is the canonical path; / api / anomalies (no trailing slash) is
        # 307-redirected by Starlette to /api/anomalies/.
        r = client.get("/api/anomalies/")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["anomalies"][0]["title"] == "Test anomaly"

    def test_summary_groups_by_type_and_severity(self, client, seeded_db):
        r = client.get("/api/anomalies/summary")
        assert r.status_code == 200
        body = r.json()
        assert body["total_anomalies"] == 1
        assert body["by_type"] == {"large_trade": 1}
        assert body["by_severity"] == {"high": 1}

    def test_get_404_for_missing_anomaly(self, client, seeded_db):
        r = client.get("/api/anomalies/999999")
        assert r.status_code == 404

    def test_admin_routes_require_auth(self, client, seeded_db):
        # /analyze, /regenerate, /cleanup all require admin token.
        for path in ("/api/anomalies/analyze", "/api/anomalies/regenerate"):
            r = client.post(path)
            assert r.status_code in (401, 403, 503), f"{path} not protected: {r.status_code}"


# ---------------- disclosures ----------------


class TestDisclosuresAPI:
    def test_list_disclosures_returns_seeded(self, client, seeded_db):
        # The disclosures router is registered at /api/disclosures (no trailing slash).
        r = client.get("/api/disclosures")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["disclosures"][0]["document_id"] == "DOC1"

    def test_filter_by_is_ptr(self, client, seeded_db):
        r = client.get("/api/disclosures?is_ptr=true")
        assert r.json()["total"] == 1
        r = client.get("/api/disclosures?is_ptr=false")
        assert r.json()["total"] == 0


# ---------------- dashboard insights ----------------


class TestInsights:
    def test_insights_returns_list(self, client, seeded_db):
        r = client.get("/api/insights")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        # On a one-member, one-trade DB we expect at least the disclosure
        # count insight (id=4) and the most-flagged-member insight (id=1).
        ids = {item["id"] for item in body}
        assert 4 in ids


# ---------------- admin auth ----------------


class TestAdminAuth:
    """Regression tests for the production auth bypass.

    `require_admin` used to accept the literal string "local-dev-token"
    unconditionally, before it read settings at all — so anyone sending that
    header got admin on every mutating endpoint of a deployed instance. The
    dev shortcut must now be gated on `not settings.is_production`.
    """

    @staticmethod
    def _settings(env: str):
        from src.config import Settings

        return Settings(ENV=env, ADMIN_PASSWORD="unit-test-password")

    def test_dev_token_rejected_in_production(self, monkeypatch):
        from fastapi import HTTPException

        from src.api import auth

        monkeypatch.setattr(auth, "get_settings", lambda: self._settings("production"))

        with pytest.raises(HTTPException) as exc:
            auth.require_admin(auth.DEV_TOKEN)
        assert exc.value.status_code == 401

    def test_dev_token_accepted_outside_production(self, monkeypatch):
        from src.api import auth

        monkeypatch.setattr(auth, "get_settings", lambda: self._settings("dev"))

        assert auth.require_admin(auth.DEV_TOKEN) == auth.DEV_TOKEN

    def test_issued_token_still_works_in_production(self, monkeypatch):
        from src.api import auth

        monkeypatch.setattr(auth, "get_settings", lambda: self._settings("production"))

        token = auth.issue_admin_token()
        try:
            assert auth.require_admin(token) == token
        finally:
            auth.revoke_admin_token(token)

    def test_unknown_token_rejected_in_production(self, monkeypatch):
        from fastapi import HTTPException

        from src.api import auth

        monkeypatch.setattr(auth, "get_settings", lambda: self._settings("production"))

        with pytest.raises(HTTPException) as exc:
            auth.require_admin("not-a-real-token")
        assert exc.value.status_code == 401


# ---------------- anomaly ordering ----------------


class TestAnomalyOrdering:
    """Severity ordering must happen in SQL, before pagination.

    The endpoint used to paginate by `detected_at DESC` and then sort only the
    rows on the current page by severity in Python, so page 1 was the newest N
    anomalies re-shuffled -- not the most severe ones. A high-severity anomaly
    that was older than a page of low-severity ones never surfaced first.
    """

    @pytest.fixture
    def graded_db(self):
        db = SessionLocal()
        for table in (Anomaly, Transaction, Asset, Disclosure, Member):
            db.query(table).delete()
        db.commit()

        member = Member(
            bioguide_id="G000001",
            first_name="Grade",
            last_name="Tester",
            chamber=Chamber.HOUSE,
            party=Party.REPUBLICAN,
            state="TX",
            in_office=True,
        )
        db.add(member)
        db.commit()
        db.refresh(member)

        # The single HIGH anomaly is the OLDEST, so any ordering that paginates
        # by detected_at first will push it off page 1.
        rows = [("high", datetime(2020, 1, 1))]
        rows += [("low", datetime(2024, 6, i + 1)) for i in range(5)]

        for severity, detected in rows:
            db.add(
                Anomaly(
                    member_id=member.id,
                    anomaly_type="large_trade",
                    severity=severity,
                    title=f"{severity} @ {detected:%Y-%m-%d}",
                    description="ordering fixture",
                    detected_at=detected,
                )
            )
        db.commit()
        yield db
        db.close()

    def test_highest_severity_appears_first_even_when_oldest(self, client, graded_db):
        r = client.get("/api/anomalies/?page_size=3")
        assert r.status_code == 200

        severities = [a["severity"] for a in r.json()["anomalies"]]
        assert severities[0] == "high", f"expected high first, got {severities}"

    def test_severity_filter_is_case_insensitive(self, client, graded_db):
        # Detectors historically wrote "HIGH", "high" and raw integers into the
        # same column, so an exact-match filter silently missed rows.
        lower = client.get("/api/anomalies/?severity=high").json()["total"]
        upper = client.get("/api/anomalies/?severity=HIGH").json()["total"]

        assert lower == upper == 1

    def test_pagination_totals_are_stable(self, client, graded_db):
        first = client.get("/api/anomalies/?page=1&page_size=2").json()
        second = client.get("/api/anomalies/?page=2&page_size=2").json()

        assert first["total"] == second["total"] == 6
        first_ids = {a["id"] for a in first["anomalies"]}
        second_ids = {a["id"] for a in second["anomalies"]}
        assert not (first_ids & second_ids), "pages must not overlap"


class TestPercentileFilter:
    """`min_percentile` is the defensible way to ask for the strongest findings.

    Detector thresholds are asserted rather than calibrated, so filtering on
    "top N% within this anomaly type" says something the raw threshold cannot.
    """

    @pytest.fixture
    def ranked_db(self):
        from src.analysis.baselines import annotate_percentile_ranks

        db = SessionLocal()
        for table in (Anomaly, Transaction, Asset, Disclosure, Member):
            db.query(table).delete()
        db.commit()

        member = Member(
            bioguide_id="P000002",
            first_name="Pct",
            last_name="Filter",
            chamber=Chamber.HOUSE,
            party=Party.DEMOCRAT,
            state="OR",
            in_office=True,
        )
        db.add(member)
        db.commit()
        db.refresh(member)

        for value in range(1, 21):
            db.add(
                Anomaly(
                    member_id=member.id,
                    anomaly_type="large_trade",
                    severity="medium",
                    title=f"finding {value}",
                    description="percentile fixture",
                    computed_value=Decimal(str(value)),
                )
            )
        db.commit()
        annotate_percentile_ranks(db)
        yield db
        db.close()

    def test_percentile_rank_is_returned(self, client, ranked_db):
        body = client.get("/api/anomalies/?page_size=100").json()
        assert all(a["percentile_rank"] is not None for a in body["anomalies"])

    def test_min_percentile_narrows_the_result(self, client, ranked_db):
        everything = client.get("/api/anomalies/").json()["total"]
        top_decile = client.get("/api/anomalies/?min_percentile=90").json()

        # Ranks over values 1..20 are v/20*100, so >=90 selects v in {18,19,20}.
        assert everything == 20
        assert top_decile["total"] == 3
        assert all(a["percentile_rank"] >= 90 for a in top_decile["anomalies"])

    def test_out_of_range_percentile_is_rejected(self, client, ranked_db):
        assert client.get("/api/anomalies/?min_percentile=101").status_code == 422
        assert client.get("/api/anomalies/?min_percentile=-1").status_code == 422
