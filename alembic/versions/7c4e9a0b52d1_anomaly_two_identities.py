"""An anomaly about a trade is identified by the trade, not by its title.

`uq_anomaly_member_type_title` (added in c3a7f1d92b04) took the dedupe key
`persist_anomalies` checks in Python and enforced it on every row. The first
analysis run over a populated database died on it:

    UniqueViolation: duplicate key value violates unique constraint
    "uq_anomaly_member_type_title"
    DETAIL:  Key (member_id, anomaly_type, title)=(12188, late_filing,
    Late PTR filing: severely late (over 3 months)) already exists.

Neither row was wrong. A `late_filing` title names a bucket, so a member with
two trades filed more than three months late produces two findings with the
same title and different transaction_ids -- and the index made that
unrepresentable. Enforcing it would have meant publishing one late trade per
member per bucket and silently dropping the others, which is a data-loss bug
wearing the costume of a constraint.

This replaces the one index with the two that are actually true, each partial
so they cannot interfere. See src/analysis/anomaly_key.py.

Revision ID: 7c4e9a0b52d1
Revises: e93c5d2a8b17
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "7c4e9a0b52d1"
down_revision: Union[str, None] = "e93c5d2a8b17"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_rows_violating(conn, key_columns: str, where: str) -> None:
    """Delete all but the earliest row of each duplicate group.

    The old index was stricter about titles and blind about transactions, so
    rows sharing (member, type, transaction) but differing in title exist and
    would fail the new index. Keeping the lowest id matches what c3a7f1d92b04
    did.
    """
    conn.execute(
        sa.text(
            f"""
            DELETE FROM anomalies a
            USING anomalies b
            WHERE {where.replace("transaction_id", "a.transaction_id")}
              AND a.id > b.id
              AND {" AND ".join(f"a.{c} = b.{c}" for c in key_columns.split(","))}
            """
        )
    )


def upgrade() -> None:
    conn = op.get_bind()
    is_postgres = conn.dialect.name == "postgresql"

    op.drop_index("uq_anomaly_member_type_title", table_name="anomalies")

    if is_postgres:
        # Only reachable on PostgreSQL; SQLite databases here are built by
        # metadata.create_all() and start empty.
        _drop_rows_violating(
            conn,
            "member_id,anomaly_type,transaction_id",
            "transaction_id IS NOT NULL",
        )
        _drop_rows_violating(
            conn,
            "member_id,anomaly_type,title",
            "transaction_id IS NULL",
        )

    op.create_index(
        "uq_anomaly_member_type_transaction",
        "anomalies",
        ["member_id", "anomaly_type", "transaction_id"],
        unique=True,
        postgresql_where=sa.text("transaction_id IS NOT NULL"),
        sqlite_where=sa.text("transaction_id IS NOT NULL"),
    )
    op.create_index(
        "uq_anomaly_member_type_title",
        "anomalies",
        ["member_id", "anomaly_type", "title"],
        unique=True,
        postgresql_where=sa.text("transaction_id IS NULL"),
        sqlite_where=sa.text("transaction_id IS NULL"),
    )


def downgrade() -> None:
    # Going back means the narrower rule applies again, so the rows it cannot
    # represent have to go -- the same trade-level findings this migration was
    # written to keep. Deleting them is what makes the downgrade re-runnable
    # rather than a constraint violation.
    conn = op.get_bind()

    op.drop_index("uq_anomaly_member_type_transaction", table_name="anomalies")
    op.drop_index("uq_anomaly_member_type_title", table_name="anomalies")

    if conn.dialect.name == "postgresql":
        conn.execute(
            sa.text(
                """
                DELETE FROM anomalies a
                USING anomalies b
                WHERE a.id > b.id
                  AND a.member_id = b.member_id
                  AND a.anomaly_type = b.anomaly_type
                  AND a.title = b.title
                """
            )
        )

    op.create_index(
        "uq_anomaly_member_type_title",
        "anomalies",
        ["member_id", "anomaly_type", "title"],
        unique=True,
    )
