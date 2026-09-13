"""Cache the SEC industry code for each traded ticker.

Revision ID: b8c4f26d90a1
Revises: a4d9e0c71b58

Every detector that joins a trade to a committee remit or a bill's policy area
asks what sector a holding belongs to, and the answer came from a hand-written
list of about 70 large-cap tickers. This caches SEC's own Standard Industrial
Classification per issuer so the question can be answered for every registrant
instead.

`sector` is nullable on purpose: most SIC codes describe industries no committee
oversees, and recording the miss is what stops the next run re-fetching it. A
ticker absent from the table means "never looked up"; a row with a null sector
means "looked up, no sector".
"""

import sqlalchemy as sa
from alembic import op

revision = "b8c4f26d90a1"
down_revision = "a4d9e0c71b58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_industries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("cik", sa.Integer(), nullable=True),
        sa.Column("sic", sa.String(length=10), nullable=True),
        sa.Column("sic_description", sa.String(length=200), nullable=True),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("sector", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_company_industries_ticker", "company_industries", ["ticker"], unique=True)
    op.create_index("ix_company_industries_cik", "company_industries", ["cik"])
    op.create_index("ix_company_industries_sic", "company_industries", ["sic"])
    op.create_index("ix_company_industries_sector", "company_industries", ["sector"])


def downgrade() -> None:
    op.drop_table("company_industries")
