"""Store Schedule H: the trips somebody else paid for.

Revision ID: d4a8b61e73c2
Revises: c1f70a2b93de

52% of House annual reports disclose at least one privately funded trip --
measured over a random sample of 70 drawn from the Clerk's 2024-25 index, 67 of
which parsed, carrying 61 dated trips between them. Nothing in this project read
Schedule H, so a member flying to Tel Aviv, Havana, Bogota or Bellagio on
somebody else's money was in the document and in no database.

The same sample rules out two of the six unread schedules outright: G (Gifts)
and I (Payments to Charity in Lieu of Honoraria) are present in all 67 filings
and read exactly "None disclosed." in all 67. Tables for those would be empty.

Three columns of the form are deliberately not here. Schedule H ends with
Lodging?, Food? and Family?, and those ticks are drawn as vector curves rather
than text -- page 6 of document 10074944 has zero characters to the right of
x=400, where all three sit. A column that could only ever be filled by guessing
at path geometry is worse than no column.

Nothing serves this table yet. It stores what the documents say; publishing it,
and building any detector over it, is a separate decision with its own evidence
bar.
"""

import sqlalchemy as sa

from alembic import op

revision = "d4a8b61e73c2"
down_revision = "c1f70a2b93de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "travel_payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("disclosure_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("start_date", sa.DateTime(), nullable=True),
        sa.Column("end_date", sa.DateTime(), nullable=True),
        sa.Column("itinerary", sa.Text(), nullable=True),
        sa.Column("days_at_own_expense", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["disclosure_id"], ["disclosures.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_travel_payments_disclosure_id"),
        "travel_payments",
        ["disclosure_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_travel_payments_disclosure_id"), table_name="travel_payments")
    op.drop_table("travel_payments")
