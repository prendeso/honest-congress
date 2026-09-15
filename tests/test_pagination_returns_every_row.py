"""Walking the list endpoints loses rows.

`GET /api/anomalies/` ordered by `(severity_rank, detected_at DESC)` and
nothing else. Both keys tie in their thousands -- there are three severities,
and a single analysis run stamps `detected_at` on every finding it writes -- so
the sort is only a partial order. `LIMIT/OFFSET` over a partial order is free to
return a row on two pages and another on none, because nothing requires the
database to break those ties the same way for page 12 as it did for page 11.

Measured against the live API on a STATIC table, with nothing writing to it:

    rows fetched  : 4888      <- matches the reported total exactly
    distinct ids  : 4881
    duplicated    : 7
    never returned: 7

The count is right, which is why nobody noticed. Anything that walks the pages
-- the site's own list, an export, an audit sampling the corpus -- silently
sees seven findings twice and seven not at all. It is also perfectly
repeatable, so a second pass agrees with the first and confirms nothing.

`/api/assets` already ordered by `Asset.id.desc()` and was never affected,
which is the whole fix: a unique final tiebreak.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.db.database import SessionLocal
from src.db.models import Anomaly, Chamber, Disclosure, Member, Party


@pytest.fixture
def seeded():
    """One severity, one timestamp, one filing year -- every sort key tied.

    That is not a contrived shape: `analyze` writes a run's findings in a
    single pass, so a real page of them shares `detected_at` to the second.
    """
    session = SessionLocal()
    for table in (Anomaly, Disclosure, Member):
        session.query(table).delete()
    session.commit()

    stamped = datetime(2026, 9, 15, 12, 0, 0)
    for n in range(60):
        member = Member(
            bioguide_id=f"P{n:06d}",
            first_name="Page",
            last_name="Member",
            chamber=Chamber.HOUSE,
            party=Party.DEMOCRAT,
            state="CA",
        )
        session.add(member)
        session.commit()

        session.add(
            Disclosure(
                member_id=member.id,
                filing_year=2025,
                filing_type="P",
                filing_date=stamped,
                document_id=f"PAGE-{n}",
                document_url=f"https://example.invalid/{n}.pdf",
                is_ptr=True,
                parsed=False,
            )
        )
        session.add(
            Anomaly(
                member_id=member.id,
                anomaly_type="large_trade",
                severity="high",
                title=f"Finding {n}",
                description="every sort key tied with its neighbours",
                detected_at=stamped,
            )
        )
        session.commit()

    yield session
    for table in (Anomaly, Disclosure, Member):
        session.query(table).delete()
    session.commit()
    session.close()


def order_by_of_the_paginated_select(path: str) -> str:
    """The ORDER BY of the statement that actually carries LIMIT/OFFSET.

    This is the assertion that bites. The page-walking tests below CANNOT fail
    on SQLite: it happens to break ties by rowid, consistently, so a walk comes
    back whole even with the defect present -- verified by removing the fix and
    watching all sixteen of them pass. The suite runs on SQLite and production
    is Postgres, whose sort is under no such obligation, so a test that only
    walks pages is a test that would have shipped this bug.

    So the mechanism is asserted instead of its symptom: the emitted ORDER BY
    must end in a column that is unique, because that is what makes LIMIT/OFFSET
    deterministic on any engine.
    """
    from sqlalchemy import event

    from src.db.database import engine

    seen: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        joiner = "" if path.endswith(("?", "&")) else "?"
        TestClient(app).get(f"{path}{joiner}page=2&page_size=7")
    finally:
        event.remove(engine, "before_cursor_execute", record)

    paginated = [s for s in seen if "LIMIT" in s.upper() and "ORDER BY" in s.upper()]
    assert paginated, f"{path} issued no paginated ORDER BY statement"

    statement = paginated[-1].upper()
    clause = statement[statement.rindex("ORDER BY") + len("ORDER BY") :]
    return clause.split("LIMIT")[0].strip()


def walk(client: TestClient, path: str, key: str, page_size: int = 7) -> tuple[list[int], int]:
    """Every id the endpoint hands out, page by page, as a client would."""
    first = client.get(f"{path}?page=1&page_size={page_size}").json()
    total = first["total"]
    ids = [row["id"] for row in first[key]]

    page = 2
    while len(ids) < total:
        body = client.get(f"{path}?page={page}&page_size={page_size}").json()
        rows = body[key]
        if not rows:
            break
        ids.extend(row["id"] for row in rows)
        page += 1

    return ids, total


@pytest.mark.parametrize(
    "path,key",
    [
        ("/api/anomalies/", "anomalies"),
        ("/api/members", "members"),
        ("/api/disclosures", "disclosures"),
        ("/api/assets", "assets"),
    ],
)
class TestEveryRowIsReturnedExactlyOnce:
    def test_no_row_is_returned_twice(self, seeded, path, key):
        client = TestClient(app)

        ids, _ = walk(client, path, key)

        duplicated = {i for i in ids if ids.count(i) > 1}
        assert not duplicated, f"{path} served {len(duplicated)} row(s) on more than one page"

    def test_no_row_is_skipped(self, seeded, path, key):
        client = TestClient(app)

        ids, total = walk(client, path, key)

        assert len(set(ids)) == total, (
            f"{path} reports {total} rows and hands out {len(set(ids))} distinct ones; "
            f"{total - len(set(ids))} are unreachable through pagination"
        )

    def test_the_page_walk_is_stable_across_passes(self, seeded, path, key):
        """Two identical walks must agree. The live defect was repeatable, so
        this alone would not have caught it -- it is here because an unstable
        order is the other way the same bug shows up."""
        client = TestClient(app)

        first, _ = walk(client, path, key)
        second, _ = walk(client, path, key)

        assert first == second


class TestTheOrderingCarriesAUniqueKey:
    """The mechanism, asserted directly: ties in every other key are what makes
    LIMIT/OFFSET non-deterministic, so the final key has to be unique."""

    @pytest.mark.parametrize(
        "path,key,sort",
        [
            ("/api/anomalies/", "anomalies", None),
            ("/api/members", "members", "anomalies"),
            ("/api/members", "members", "disclosures"),
            ("/api/disclosures", "disclosures", "filing_year"),
        ],
    )
    def test_a_sorted_walk_still_returns_everything(self, seeded, path, key, sort):
        """Every sort option, not just the default: each one is its own
        ORDER BY, and a tiebreak on one does not fix another."""
        client = TestClient(app)
        suffix = f"&sort_by={sort}" if sort else ""

        first = client.get(f"{path}?page=1&page_size=7{suffix}").json()
        total = first["total"]
        ids = [r["id"] for r in first[key]]
        page = 2
        while len(ids) < total:
            rows = client.get(f"{path}?page={page}&page_size=7{suffix}").json()[key]
            if not rows:
                break
            ids.extend(r["id"] for r in rows)
            page += 1

        assert len(set(ids)) == total


class TestTheEmittedOrderByEndsInAUniqueColumn:
    """The falsifiable half. Removing any of the three tiebreaks fails this."""

    @pytest.mark.parametrize(
        "path,table",
        [
            ("/api/anomalies/", "ANOMALIES"),
            ("/api/members", "MEMBERS"),
            ("/api/disclosures", "DISCLOSURES"),
            ("/api/assets", "ASSETS"),
        ],
    )
    def test_the_last_sort_key_is_the_primary_key(self, seeded, path, table):
        clause = order_by_of_the_paginated_select(path)

        last = clause.split(",")[-1].strip()
        assert last.startswith(f"{table}.ID"), (
            f"{path} paginates on `ORDER BY {clause}`, whose final key is not "
            f"unique. Rows tied on every key it does name can be returned on "
            f"two pages and on none -- measured live as 7 duplicated and 7 "
            f"unreachable out of 4,888."
        )

    @pytest.mark.parametrize(
        "path,table,sort",
        [
            ("/api/members", "MEMBERS", "anomalies"),
            ("/api/members", "MEMBERS", "disclosures"),
            ("/api/members", "MEMBERS", "name"),
            ("/api/disclosures", "DISCLOSURES", "filing_year"),
            ("/api/disclosures", "DISCLOSURES", "filing_date"),
        ],
    )
    def test_every_sort_option_keeps_the_tiebreak(self, seeded, path, table, sort):
        """Each `sort_by` builds its own ORDER BY, and a tiebreak appended to
        one branch does nothing for the others. `sort_by=anomalies` over a
        roster where most members have zero is nearly all ties."""
        clause = order_by_of_the_paginated_select(f"{path}?sort_by={sort}&")

        assert clause.split(",")[-1].strip().startswith(f"{table}.ID")
