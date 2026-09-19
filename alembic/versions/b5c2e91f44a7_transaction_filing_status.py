"""Store the row's own Filing Status, which the House PTR prints and we discarded

The form marks every row "New" or "Amended" in the footnote beneath it. An
amended row restates one already disclosed, so scoring it against the STOCK
Act's 45-day clock re-accuses a filing that was on time -- Rep. Keating
disclosed a 13 September 2023 sale on 28 September, 15 days, and the amendment
carrying that row again was published as "filed significantly late (1-3
months)", 125 days.

Nullable, and left NULL for every row already stored: the value has to be read
from the document, so a re-parse fills it and nothing is invented here.

Revision ID: b5c2e91f44a7
Revises: d4a8b61e73c2
"""

import sqlalchemy as sa
from alembic import op

revision = "b5c2e91f44a7"
down_revision = "d4a8b61e73c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("filing_status", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "filing_status")
