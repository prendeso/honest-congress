"""Store the Notification Date the House PTR prints and we discarded

Every House PTR prints, beside the transaction date, the date the filer says
they were notified of the trade. `ptr_parser` has parsed it since the golden
tests were written; `Transaction` had no column for it and both
`_store_ptr_data` call sites dropped it on the floor.

It does NOT move the deadline. 5 U.S.C. 13104(l) requires a report within 30
days of notification "but in no case later than 45 days after such
transaction", so a late notification can only shorten a filer's window, never
extend it past 45 days. The column exists so a finding can say WHY a filing was
late -- often the broker rather than the filer -- not so any finding can be
suppressed.

Nullable, and left NULL for every row already stored: the value has to be read
from the document, so a re-parse fills it and nothing is invented here. NULL
means "not read yet", never "no notification".

Revision ID: c7f4a2e68b91
Revises: b5c2e91f44a7
"""

import sqlalchemy as sa
from alembic import op

revision = "c7f4a2e68b91"
down_revision = "b5c2e91f44a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("notification_date", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "notification_date")
