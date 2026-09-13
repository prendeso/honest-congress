"""Normalize anomaly severity and add the missing uniqueness guarantee.

Two problems with the `anomalies` table:

1. `severity` is free text and three detector families wrote three different
   vocabularies into it -- "HIGH"/"CRITICAL", "high"/"medium"/"low", and raw
   integer scores 4-10 stringified. The API filters with
   `severity == value.lower()`, so every "HIGH" row was invisible to
   `?severity=high`. This normalizes existing rows; new writes are normalized
   on the model itself (see Anomaly._validate_severity).

2. `persist_anomalies` deduplicates on (member_id, anomaly_type, title) with a
   SELECT-then-INSERT, but nothing enforced it in the schema, so concurrent
   runs -- or the two analyzers that bypass persist_anomalies entirely -- could
   insert duplicates. This removes existing duplicates (keeping the earliest
   row of each group) and adds the unique index.

Revision ID: c3a7f1d92b04
Revises: 61801c568bd7
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c3a7f1d92b04"
down_revision: Union[str, None] = "61801c568bd7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Text values that map onto the canonical vocabulary.
_TEXT_MAP = {
    "critical": "high",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "CRITICAL": "high",
    "Critical": "high",
    "High": "high",
    "Medium": "medium",
    "Low": "low",
}


def upgrade() -> None:
    anomalies = sa.table(
        "anomalies",
        sa.column("id", sa.Integer),
        sa.column("member_id", sa.Integer),
        sa.column("anomaly_type", sa.String),
        sa.column("severity", sa.String),
        sa.column("title", sa.String),
    )

    conn = op.get_bind()

    # --- 1. normalize severity -------------------------------------------------
    for raw, canonical in _TEXT_MAP.items():
        conn.execute(
            anomalies.update().where(anomalies.c.severity == raw).values(severity=canonical)
        )

    # Integer scores were stringified by the ORM: >=8 high, >=5 medium, else low.
    for value in range(0, 11):
        canonical = "high" if value >= 8 else "medium" if value >= 5 else "low"
        conn.execute(
            anomalies.update().where(anomalies.c.severity == str(value)).values(severity=canonical)
        )

    # Anything still outside the vocabulary becomes "medium", matching
    # normalize_severity()'s fallback rather than leaving unfilterable rows.
    conn.execute(
        anomalies.update()
        .where(anomalies.c.severity.notin_(["low", "medium", "high"]))
        .values(severity="medium")
    )

    # --- 2. drop duplicates, keeping the earliest row of each group -------------
    conn.execute(
        sa.text(
            """
            DELETE FROM anomalies
            WHERE id NOT IN (
                SELECT MIN(id) FROM anomalies
                GROUP BY member_id, anomaly_type, title
            )
            """
        )
    )

    # --- 3. enforce it ---------------------------------------------------------
    op.create_index(
        "uq_anomaly_member_type_title",
        "anomalies",
        ["member_id", "anomaly_type", "title"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_anomaly_member_type_title", table_name="anomalies")
    # Severity normalization is not reversed: the original mixed vocabularies
    # are not recoverable, and the canonical values are valid input anyway.
