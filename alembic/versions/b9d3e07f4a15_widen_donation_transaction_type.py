"""FEC's transaction type is prose, and 50 characters could not hold it.

The donation ingest spent seventeen minutes calling the FEC API and then threw
all of it away on its final commit:

    DataError: value too long for type character varying(50)
    transaction_type: 'CONTRIBUTION RECEIVED FROM REGISTERED FILER (CANDIDATES)'

That string is 55 characters. FEC's vocabulary is descriptive rather than coded,
so several of its values exceed 50, and the step is `continue-on-error` in both
workflows -- it reported success while storing nothing, and `donor_conflict` sat
at zero findings with no indication why.

Widened rather than truncated: the value distinguishes a direct contribution
from an earmarked one and from a transfer, which is exactly the kind of
distinction a donation-to-trade detector should keep. `donor_name` alongside it
is already String(255).

Revision ID: b9d3e07f4a15
Revises: 7c4e9a0b52d1
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b9d3e07f4a15"
down_revision: Union[str, None] = "7c4e9a0b52d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "campaign_donations",
        "transaction_type",
        existing_type=sa.String(length=50),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Narrowing again would fail on any row already storing a longer value, so
    # trim first. A downgrade that cannot run is not a downgrade.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "UPDATE campaign_donations "
            "SET transaction_type = LEFT(transaction_type, 50) "
            "WHERE transaction_type IS NOT NULL AND LENGTH(transaction_type) > 50"
        )

    op.alter_column(
        "campaign_donations",
        "transaction_type",
        existing_type=sa.String(length=255),
        type_=sa.String(length=50),
        existing_nullable=True,
    )
