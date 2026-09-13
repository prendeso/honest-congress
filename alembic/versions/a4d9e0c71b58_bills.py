"""Add bills, sponsorships and committee referrals.

Revision ID: a4d9e0c71b58
Revises: f7b3c2a51d90

Every table before this one describes trading. These three describe what a
member did in office, so trades can be joined to legislative action -- the one
thing the free trade-listing sites do not publish.

`bills.policy_area` is nullable because 29% of real bills carry none: CRS has
not classified them yet. `bills.committees_fetched` exists because committee
referrals cost one request per bill and are only worth spending on bills that
already matched a member's trading, so "no committees" and "not looked up yet"
must stay distinguishable.
"""

import sqlalchemy as sa
from alembic import op

revision = "a4d9e0c71b58"
down_revision = "f7b3c2a51d90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bills",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("congress", sa.Integer(), nullable=False),
        sa.Column("bill_type", sa.String(length=10), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("policy_area", sa.String(length=120), nullable=True),
        sa.Column("origin_chamber", sa.String(length=20), nullable=True),
        sa.Column("introduced_date", sa.DateTime(), nullable=True),
        sa.Column("latest_action_date", sa.DateTime(), nullable=True),
        sa.Column("latest_action_text", sa.Text(), nullable=True),
        sa.Column("committees_fetched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bills_congress", "bills", ["congress"])
    op.create_index("ix_bills_policy_area", "bills", ["policy_area"])
    op.create_index("ix_bills_introduced_date", "bills", ["introduced_date"])
    op.create_index("uq_bill_identity", "bills", ["congress", "bill_type", "number"], unique=True)

    op.create_table(
        "bill_sponsorships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bill_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("is_sponsor", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["bill_id"], ["bills.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bill_sponsorships_bill_id", "bill_sponsorships", ["bill_id"])
    op.create_index("ix_bill_sponsorships_member_id", "bill_sponsorships", ["member_id"])
    op.create_index("ix_bill_sponsorships_is_sponsor", "bill_sponsorships", ["is_sponsor"])
    op.create_index(
        "uq_bill_sponsorship",
        "bill_sponsorships",
        ["bill_id", "member_id", "is_sponsor"],
        unique=True,
    )

    op.create_table(
        "bill_committees",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bill_id", sa.Integer(), nullable=False),
        sa.Column("committee_id", sa.String(length=20), nullable=False),
        sa.Column("committee_name", sa.String(length=200), nullable=True),
        sa.Column("chamber", sa.String(length=20), nullable=True),
        sa.Column("activity", sa.String(length=100), nullable=True),
        sa.Column("activity_date", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["bill_id"], ["bills.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bill_committees_bill_id", "bill_committees", ["bill_id"])
    op.create_index("ix_bill_committees_committee_id", "bill_committees", ["committee_id"])
    op.create_index("ix_bill_committees_activity_date", "bill_committees", ["activity_date"])
    op.create_index(
        "uq_bill_committee",
        "bill_committees",
        ["bill_id", "committee_id", "activity", "activity_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("bill_committees")
    op.drop_table("bill_sponsorships")
    op.drop_table("bills")
