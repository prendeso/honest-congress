"""Store the filer's own Comment, which the Senate's eFD prints and we dropped

`senate_html_parser._COLUMN_ALIASES` has always mapped eFD's Comment column;
`_rows_to_transactions` read the value and never put it in the dict, so nothing
downstream could see it.

Two rows in the local corpus read "While no immediate PTR required, provided to
clearly denote basis for the renamed asset" -- Sen. Hagerty's Crestwood/Energy
Transfer and Equitrans/EQT corporate actions -- and both are published as late
STOCK Act filings. The column exists so the finding can show what the filer
wrote. Nothing scores against it: an accusation is not withdrawn on the
strength of the accused's own note.

NULL means the source has no such column (every House PTR) or the row predates
this. Nullable Text, because eFD imposes no length this project knows.

Revision ID: d1a7c39e50b8
Revises: c7f4a2e68b91
"""

import sqlalchemy as sa
from alembic import op

revision = "d1a7c39e50b8"
down_revision = "c7f4a2e68b91"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("filer_comment", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "filer_comment")
