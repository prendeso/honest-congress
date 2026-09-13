"""Record whether a filing's PDF had a text layer at all.

Revision ID: e93c5d2a8b17
Revises: d5a91c308f7e

`parse_confidence` already says how much of a filing was read. It cannot say
*why* nothing was, and those two failures need different answers: a filing the
parser mishandled is a bug to fix, and a scan of a paper form is a boundary of
the source data that no parser change will ever move.

123 of the 966 House PTRs filed in 2024-25 -- 12.7% -- are scans with no
extractable text. Folding them into the parser's failure count overstated it
eightfold and buried the fact worth publishing: roughly one House trade report
in eight is not in the machine-readable dataset at all.

Nullable, and null means unknown -- parsed before this was recorded -- rather
than "had text".
"""

import sqlalchemy as sa
from alembic import op

revision = "e93c5d2a8b17"
down_revision = "d5a91c308f7e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("disclosures", sa.Column("has_text_layer", sa.Boolean(), nullable=True))
    op.create_index("ix_disclosures_has_text_layer", "disclosures", ["has_text_layer"])


def downgrade() -> None:
    op.drop_index("ix_disclosures_has_text_layer", table_name="disclosures")
    op.drop_column("disclosures", "has_text_layer")
