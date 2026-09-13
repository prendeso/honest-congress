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


class TestEveryWriteIsBehindAdminAuth:
    """Enumerate the routes rather than trusting that each one remembered.

    `POST /api/anomalies/{id}/review` shipped with no `require_admin`
    dependency at all -- `require_admin` was not even imported into
    `query.py` -- so any unauthenticated caller could write `reviewed = True`
    to the production database. Nothing caught it because every test that
    existed tested `require_admin` itself, and that function was fine. The
    hole was a route that never called it.

    So this asserts the property directly, over every route the app actually
    registers. A new write endpoint that forgets the dependency fails here.
    """

    #: The only mutating route that must stay open: it is how you authenticate.
    #: It does its own checking -- 503 with no password configured, 401 on a
    #: wrong one (`routes/anomalies/admin.py`).
    PUBLIC_BY_DESIGN = {"/api/anomalies/admin/login"}

    WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    @classmethod
    def _write_routes(cls, routes, prefix=""):
        """Every write route and the dependencies guarding it, fully qualified.

        This FastAPI version does not flatten `include_router` into
        `app.routes` -- it keeps `_IncludedRouter` wrappers, whose real routes
        hang off `original_router` and whose prefix lives on `include_context`.
        A walker that assumed the flat list found nothing at all, which is the
        failure mode `test_the_walk_actually_finds_the_routes` exists to catch.
        """
        for route in routes:
            if type(route).__name__ == "_IncludedRouter":
                nested = getattr(route.include_context, "prefix", "") or ""
                yield from cls._write_routes(route.original_router.routes, prefix + nested)
                continue

            nested_routes = getattr(route, "routes", None)
            if nested_routes:
                own = getattr(route, "prefix", "") or ""
                yield from cls._write_routes(nested_routes, prefix + own)
                continue

            methods = getattr(route, "methods", None) or set()
            if not methods & cls.WRITE_METHODS:
                continue

            guards = {
                dependency.call.__name__
                for dependency in route.dependant.dependencies
                if getattr(dependency, "call", None) is not None
            }
            yield sorted(methods & cls.WRITE_METHODS)[0], prefix + route.path, guards

    def test_no_write_route_is_unauthenticated(self):
        from src.api.main import app

        unguarded = [
            f"{method} {path}"
            for method, path, guards in self._write_routes(app.routes)
            if path not in self.PUBLIC_BY_DESIGN and "require_admin" not in guards
        ]

        assert not unguarded, (
            f"these routes write and have no require_admin dependency: {unguarded}. "
            "Add `_: str = Depends(require_admin)`, or list the route in "
            "PUBLIC_BY_DESIGN with a reason."
        )

    def test_the_walk_actually_finds_the_routes(self):
        """Without this, a walker that matches nothing passes the test above."""
        from src.api.main import app

        found = {path: guards for _, path, guards in self._write_routes(app.routes)}

        assert len(found) >= 8, f"expected at least 8 write routes, walked {sorted(found)}"
        assert "/api/anomalies/{anomaly_id}/review" in found, (
            "the route this class was written for is not being reached by the walk"
        )
        assert "require_admin" in found["/api/anomalies/{anomaly_id}/review"]


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


class TestFdrFiltering:
    """The default list must hide weak coincidences without hiding whole detectors.

    Ten of the sixteen detectors measure a magnitude and have no null model, so
    a bare `q_value <= alpha` filter would drop them from every default
    response. A null q_value means untested, never failed.
    """

    def _anomaly(self, db, anomaly_type, title, q_value):
        member_id = db.query(Member).first().id
        db.add(
            Anomaly(
                member_id=member_id,
                anomaly_type=anomaly_type,
                severity="HIGH",
                title=title,
                description=title,
                q_value=q_value,
                p_value=q_value,
            )
        )
        db.commit()

    def test_untested_findings_are_returned_by_default(self, client, seeded_db):
        self._anomaly(seeded_db, "sector_concentration", "fdr-untested", None)

        titles = [a["title"] for a in client.get("/api/anomalies/").json()["anomalies"]]
        assert "fdr-untested" in titles

    def test_findings_failing_fdr_are_hidden_by_default(self, client, seeded_db):
        self._anomaly(seeded_db, "donor_conflict", "fdr-failed", 0.8)

        titles = [a["title"] for a in client.get("/api/anomalies/").json()["anomalies"]]
        assert "fdr-failed" not in titles

    def test_findings_passing_fdr_are_returned(self, client, seeded_db):
        self._anomaly(seeded_db, "donor_conflict", "fdr-passed", 0.001)

        titles = [a["title"] for a in client.get("/api/anomalies/").json()["anomalies"]]
        assert "fdr-passed" in titles

    def test_failures_can_be_asked_for_explicitly(self, client, seeded_db):
        self._anomaly(seeded_db, "donor_conflict", "fdr-optin", 0.8)

        response = client.get("/api/anomalies/?include_below_fdr=true")
        assert "fdr-optin" in [a["title"] for a in response.json()["anomalies"]]

    def test_the_response_says_whether_a_null_model_existed(self, client, seeded_db):
        self._anomaly(seeded_db, "donor_conflict", "fdr-tested", 0.001)
        self._anomaly(seeded_db, "large_trade", "fdr-no-model", None)

        by_title = {a["title"]: a for a in client.get("/api/anomalies/").json()["anomalies"]}
        assert by_title["fdr-tested"]["has_null_model"] is True
        assert by_title["fdr-tested"]["q_value"] == pytest.approx(0.001)
        assert by_title["fdr-no-model"]["has_null_model"] is False
        assert by_title["fdr-no-model"]["q_value"] is None


class TestParseConfidenceFiltering:
    """`parsed` says the parser ran. The score says whether it worked."""

    def _disclosure(self, db, document_id, confidence, warnings=None):
        from src.db.models import Disclosure

        db.add(
            Disclosure(
                member_id=db.query(Member).first().id,
                filing_year=2024,
                filing_type="PTR",
                filing_date=datetime(2024, 6, 1),
                document_id=document_id,
                is_ptr=True,
                parsed=True,
                parse_confidence=confidence,
                parse_warnings=warnings,
            )
        )
        db.commit()

    def test_the_response_carries_the_score_and_its_reasons(self, client, seeded_db):
        self._disclosure(seeded_db, "CONF1", 0.75, "1 transaction(s) missing amount_min")

        rows = {d["document_id"]: d for d in client.get("/api/disclosures/").json()["disclosures"]}
        assert rows["CONF1"]["parse_confidence"] == pytest.approx(0.75)
        assert "missing amount_min" in rows["CONF1"]["parse_warnings"]

    def test_filtering_finds_the_filings_that_parsed_badly(self, client, seeded_db):
        self._disclosure(seeded_db, "CONF_GOOD", 1.0)
        self._disclosure(seeded_db, "CONF_BAD", 0.4)

        ids = [
            d["document_id"]
            for d in client.get("/api/disclosures/?max_confidence=0.5").json()["disclosures"]
        ]
        assert "CONF_BAD" in ids
        assert "CONF_GOOD" not in ids

    def test_unscored_filings_are_not_treated_as_zero(self, client, seeded_db):
        # Parsed before scoring existed. Including them would bury the real low
        # scorers under filings nobody has looked at yet.
        self._disclosure(seeded_db, "CONF_UNSCORED", None)

        ids = [
            d["document_id"]
            for d in client.get("/api/disclosures/?max_confidence=0.5").json()["disclosures"]
        ]
        assert "CONF_UNSCORED" not in ids


class TestTheParsedDocumentsPageHasSomethingToShow:
    """`/api/disclosures` never returned the counts the page was reading.

    The Parsed Documents table has three columns -- Assets, Transactions,
    Liabilities -- and three sort controls over them, all bound to
    `doc.asset_count`, `doc.transaction_count` and `doc.liability_count`. None
    of those fields has ever been in the response, so every row on the page
    whose entire subject is what was extracted read 0, 0, 0, and all three
    sorts silently did nothing.

    That is also the exact confusion D12 exists to remove: a filing read
    cleanly and a filing that yielded nothing displayed identically.
    """

    def test_the_list_reports_what_was_extracted(self, client, seeded_db):
        body = client.get("/api/disclosures").json()
        doc = next(d for d in body["disclosures"] if d["document_id"] == "DOC1")

        # The fixture seeds one transaction and one asset against DOC1, and no
        # liability -- so this distinguishes a real count from a constant.
        assert doc["transaction_count"] == 1
        assert doc["asset_count"] == 1
        assert doc["liability_count"] == 0

    def test_the_detail_view_agrees_with_the_list(self, client, seeded_db):
        listed = next(
            d
            for d in client.get("/api/disclosures").json()["disclosures"]
            if d["document_id"] == "DOC1"
        )
        detail = client.get(f"/api/disclosures/{listed['id']}").json()

        assert detail["transaction_count"] == len(detail["transactions"])
        assert detail["asset_count"] == len(detail["assets"])
        assert detail["liability_count"] == len(detail["liabilities"])

    def test_counting_does_not_cost_a_query_per_row(self, client, seeded_db):
        """Three grouped queries, not three per filing.

        Fifty rows a page times three counts is a hundred and fifty round trips
        to render one table, and the naive fix is the one that gets written.
        """
        from sqlalchemy import event

        from src.db import engine

        statements: list[str] = []

        def record(conn, cursor, statement, params, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            client.get("/api/disclosures?page_size=50")
        finally:
            event.remove(engine, "before_cursor_execute", record)

        counting = [s for s in statements if "count(" in s.lower() and "GROUP BY" in s]
        assert len(counting) <= 3, f"{len(counting)} grouped count queries: {counting}"
