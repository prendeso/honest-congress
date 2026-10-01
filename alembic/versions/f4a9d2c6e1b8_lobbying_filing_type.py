"""Store the LDA filing type, so an amendment is not read as fresh lobbying

`detect_lobbying_overlaps` measures a trade against a lobbying filing's posted
date. Three of twelve audited findings were measured against an AMENDMENT --
ONEOK's posted 2025-04-28 amends its Q4 2024 report; Uber's 2025-10-23 amends
Q1 and Q2 -- whose posting date says when a correction was made, not when
anyone lobbied. The LDA also accepts "No Activity" reports, which state that
there was no lobbying at all in the period.

The type was in every API response and never stored. NULL means a row ingested
before this column existed; the next `ingest-lobbying` run fills it in.

Revision ID: f4a9d2c6e1b8
Revises: e2c8f1a4d7b3
"""

import sqlalchemy as sa

from alembic import op

revision = "f4a9d2c6e1b8"
down_revision = "e2c8f1a4d7b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lobbying_disclosures",
        sa.Column("filing_type", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("lobbying_disclosures", "filing_type")
