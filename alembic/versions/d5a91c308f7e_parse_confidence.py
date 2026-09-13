"""Record how much of each filing the parser actually read.

Revision ID: d5a91c308f7e
Revises: c1f8a37e2b64

Every trade in the system arrives through the PDF parser, and a disclosure was
marked `parsed = True` on any run that did not raise -- including one that
extracted nothing. These two columns make "we ran the parser" and "the parse
worked" different claims.

Both are nullable, and null means not scored yet rather than scored and fine.
Filings parsed before this migration keep a null until they are re-parsed.
"""

import sqlalchemy as sa
from alembic import op

revision = "d5a91c308f7e"
down_revision = "c1f8a37e2b64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("disclosures", sa.Column("parse_confidence", sa.Float(), nullable=True))
    op.add_column("disclosures", sa.Column("parse_warnings", sa.Text(), nullable=True))
    op.create_index("ix_disclosures_parse_confidence", "disclosures", ["parse_confidence"])


def downgrade() -> None:
    op.drop_index("ix_disclosures_parse_confidence", table_name="disclosures")
    op.drop_column("disclosures", "parse_warnings")
    op.drop_column("disclosures", "parse_confidence")
