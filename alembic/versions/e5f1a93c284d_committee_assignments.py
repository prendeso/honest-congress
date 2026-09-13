"""Add committee_assignments.

`detect_committee_conflicts` had no committee data at all --
SAMPLE_COMMITTEE_ASSIGNMENTS was an empty dict -- so it substring-matched
tickers against sector keywords, where "ba" matched "Alibaba". This table holds
real assignments from unitedstates/congress-legislators, which is public domain
and keys on bioguide IDs.

Revision ID: e5f1a93c284d
Revises: d8e2b4c71f36
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e5f1a93c284d"
down_revision: Union[str, None] = "d8e2b4c71f36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _chamber_type() -> sa.types.TypeEngine:
    """The `chamber` enum, referenced rather than created.

    `fa8667552e22` already creates this type, for `members.chamber`. Declaring
    a bare `sa.Enum(name="chamber")` here made SQLAlchemy emit a second
    `CREATE TYPE chamber`, and on Postgres that is a hard error:

        psycopg.errors.DuplicateObject: type "chamber" already exists

    It blocked every deployment -- Railway runs `alembic upgrade head` as its
    pre-deploy step, so the container never started and the previous release
    kept serving.

    Two things hid it. SQLite has no native enum types, so the entire test
    suite (which builds its schema with `Base.metadata.create_all` on SQLite
    and never runs alembic at all) could not have caught it. And SQLAlchemy
    memoises enum creation per invocation: running the whole chain from empty,
    the baseline creates the type in the same run and this migration skips it,
    so a from-scratch build passes. Only an INCREMENTAL upgrade fails -- where
    the baseline ran in some earlier deploy and left no memo -- which is every
    real deployment and none of the obvious local checks.

    `create_type=False` says "this type exists, just use it". Correct on both
    paths, because the chain is linear and the baseline always precedes this.
    """
    if op.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects import postgresql

        return postgresql.ENUM("HOUSE", "SENATE", name="chamber", create_type=False)
    return sa.Enum("HOUSE", "SENATE", name="chamber")


def upgrade() -> None:
    op.create_table(
        "committee_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("committee_id", sa.String(length=20), nullable=False),
        sa.Column("committee_name", sa.String(length=200), nullable=False),
        sa.Column("chamber", _chamber_type(), nullable=True),
        sa.Column("is_subcommittee", sa.Boolean(), nullable=False),
        sa.Column("parent_committee_id", sa.String(length=20), nullable=True),
        sa.Column("title", sa.String(length=100), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("party", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_committee_assignments_member_id"),
        "committee_assignments",
        ["member_id"],
    )
    op.create_index(
        op.f("ix_committee_assignments_committee_id"),
        "committee_assignments",
        ["committee_id"],
    )
    op.create_index(
        op.f("ix_committee_assignments_parent_committee_id"),
        "committee_assignments",
        ["parent_committee_id"],
    )
    op.create_index(
        "uq_committee_assignment",
        "committee_assignments",
        ["member_id", "committee_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("committee_assignments")
