"""`cli init` is the first command in the README, and it did not work.

SQLite has no `ALTER COLUMN ... TYPE`. Revision `b9d3e07f4a15` widened
`campaign_donations.transaction_type` with a bare `op.alter_column`, which
emits

    ALTER TABLE campaign_donations ALTER COLUMN transaction_type TYPE VARCHAR(255)

PostgreSQL runs that. SQLite answers `near "ALTER": syntax error`, so `init`
died mid-chain with 15 of the 16 tables created and `travel_payments` missing --
after which every House annual parse errors out. `alembic_version` was left
stamped at the revision BEFORE the failure, so re-running reproduced it exactly.

**The suite could not have caught it.** `tests/conftest.py` builds its schema
with `Base.metadata.create_all`, which skips Alembic entirely, so every test
passed against a schema no documented command can produce -- and a database
built that way has no `alembic_version` row and can never be migrated forward.

So this test goes THROUGH `cmd_init`, not around it. A test that calls
`create_all` here would be the bug, restated.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
from argparse import Namespace

import pytest
from sqlalchemy import create_engine, inspect

from src.cli import cmd_init
from src.db.models import Base


@pytest.fixture
def fresh_sqlite(tmp_path, monkeypatch):
    """A database URL pointing at a file that does not exist yet.

    `get_settings` is `lru_cache`d and `alembic/env.py` reads the URL through
    it, so the cache has to be cleared on the way in and on the way out or the
    override leaks into the next test.
    """
    from src.config import get_settings

    path = tmp_path / "init.db"
    monkeypatch.setitem(os.environ, "DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def _tables(path) -> set[str]:
    return set(inspect(create_engine(f"sqlite:///{path}")).get_table_names())


class TestInitOnSqlite:
    def test_it_creates_every_table_the_models_declare(self, fresh_sqlite):
        cmd_init(Namespace())

        missing = {t.name for t in Base.metadata.sorted_tables} - _tables(fresh_sqlite)
        assert not missing, (
            f"`cli init` finished without these tables: {sorted(missing)}. "
            "A migration that only PostgreSQL can run leaves the chain "
            "half-applied and every later revision unreached."
        )

    def test_it_leaves_the_database_migratable(self, fresh_sqlite):
        """The half of this that `create_all` can never give you.

        A schema built outside Alembic has no version row, so the next
        migration has nothing to apply itself to.
        """
        cmd_init(Namespace())

        with sqlite3.connect(fresh_sqlite) as conn:
            stamped = conn.execute("select version_num from alembic_version").fetchall()

        assert len(stamped) == 1, stamped
        assert stamped[0][0], "alembic_version exists but is empty"

    def test_the_widened_column_is_actually_wide(self, fresh_sqlite):
        """The revision's own purpose, verified on the dialect it broke.

        FEC sends `CONTRIBUTION RECEIVED FROM REGISTERED FILER (CANDIDATES)`,
        55 characters. SQLite does not enforce VARCHAR lengths, so the type is
        checked directly rather than by inserting a long value -- which would
        pass whatever the column says.
        """
        cmd_init(Namespace())

        with sqlite3.connect(fresh_sqlite) as conn:
            columns = conn.execute("PRAGMA table_info(campaign_donations)").fetchall()

        declared = {c[1]: c[2] for c in columns}
        assert declared["transaction_type"] == "VARCHAR(255)", declared["transaction_type"]

    def test_running_it_twice_is_a_no_op(self, fresh_sqlite):
        cmd_init(Namespace())
        before = _tables(fresh_sqlite)

        cmd_init(Namespace())

        assert _tables(fresh_sqlite) == before


class TestPostgresStillGetsThePlainAlter:
    """`recreate="auto"` and not `"always"`, and this is why.

    Batch mode recreates only where the dialect cannot do the job in place. On
    PostgreSQL that means the migration emits exactly the single statement it
    always emitted, so a fix for SQLite costs the production backend nothing --
    no table rebuild, no row copy, no lock held over a copy.

    `"always"` would also break something real: it cannot run in `--sql`
    (offline) mode at all, because a move-and-copy needs to reflect the live
    table. Generating SQL for a DBA to review is the one deployment path that
    has no database to reflect.

    Offline mode is what makes this testable with no server: alembic renders
    against the dialect named in the URL and never connects.
    """

    REVISION_RANGE = "7c4e9a0b52d1:b9d3e07f4a15"

    def _offline_sql(self, monkeypatch) -> str:
        import io
        from contextlib import redirect_stdout

        from alembic.config import Config

        from alembic import command
        from src.config import get_settings

        monkeypatch.setitem(
            os.environ, "DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/nodb"
        )
        get_settings.cache_clear()
        try:
            repo_root = pathlib.Path(__file__).resolve().parent.parent
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                command.upgrade(
                    Config(str(repo_root / "alembic.ini")), self.REVISION_RANGE, sql=True
                )
            return buffer.getvalue()
        finally:
            get_settings.cache_clear()

    def test_it_is_still_one_plain_alter(self, monkeypatch):
        sql = self._offline_sql(monkeypatch)

        statements = [
            line.strip()
            for line in sql.splitlines()
            if "campaign_donations" in line and "alembic_version" not in line
        ]

        assert statements == [
            "ALTER TABLE campaign_donations ALTER COLUMN transaction_type TYPE VARCHAR(255);"
        ], statements

    def test_nothing_is_copied_or_renamed(self, monkeypatch):
        """The signature of a move-and-copy, which must not appear here."""
        sql = self._offline_sql(monkeypatch).lower()

        for forbidden in ("_alembic_tmp", "insert into campaign_donations", "rename to"):
            assert forbidden not in sql, f"{forbidden!r} means the table was rebuilt"


class TestTheWideningDoesNotEatTheRows:
    """`batch_alter_table` REBUILDS the table on SQLite -- new table, copy,
    drop, rename. That is the only way that dialect changes a column type, and
    it is also the only way this fix could silently destroy data.

    A fresh `init` migrates an empty table, so nothing here is at risk on the
    path that prompted the fix. Anyone already running SQLite with data is, and
    they are exactly the contributor this is meant to serve.
    """

    BEFORE_THE_WIDENING = "7c4e9a0b52d1"

    def test_an_existing_donation_survives(self, fresh_sqlite):
        import sqlite3
        from argparse import Namespace as _NS

        from alembic.config import Config

        from alembic import command
        from src.cli import cmd_init

        repo_root = pathlib.Path(__file__).resolve().parent.parent
        command.upgrade(Config(str(repo_root / "alembic.ini")), self.BEFORE_THE_WIDENING)

        with sqlite3.connect(fresh_sqlite) as conn:
            conn.execute(
                "insert into members (id, bioguide_id, first_name, last_name, chamber,"
                " party, state, in_office, anomaly_count, disclosure_count, created_at,"
                " updated_at) values (1,'X000001','A','B','HOUSE','DEMOCRAT','CA',1,0,0,"
                "'2024-01-01','2024-01-01')"
            )
            conn.execute(
                "insert into campaign_donations (id, member_id, donor_name, amount,"
                " transaction_type, source, created_at)"
                " values (1, 1, 'ACME PAC', 2500, 'CONTRIBUTION', 'fec', '2024-01-01')"
            )

        cmd_init(_NS())

        with sqlite3.connect(fresh_sqlite) as conn:
            rows = conn.execute(
                "select id, donor_name, transaction_type from campaign_donations"
            ).fetchall()
            declared = {c[1]: c[2] for c in conn.execute("PRAGMA table_info(campaign_donations)")}

        assert rows == [(1, "ACME PAC", "CONTRIBUTION")], "the rebuild dropped the row"
        assert declared["transaction_type"] == "VARCHAR(255)"


def test_conftest_does_not_stand_in_for_this():
    """A guard on the guard.

    The reason this bug survived is that the suite's own schema comes from
    `Base.metadata.create_all`. That is the right call for 1,700 fast tests and
    the wrong one for the question here, so if this file ever starts doing the
    same it stops testing anything.
    """
    import ast
    import inspect as _inspect

    import tests.test_init_builds_the_whole_schema as module

    # Docstrings are excluded: this file names the shortcut repeatedly in order
    # to explain why it is wrong, and a rule that forbade saying so would
    # delete the explanation along with the guard. What must not appear is a
    # CALL to it.
    tree = ast.parse(_inspect.getsource(module))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "create_all" not in called, (
        "this file must exercise `cmd_init`; building the schema directly is the bug"
    )
