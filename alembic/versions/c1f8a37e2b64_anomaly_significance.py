"""Add p-value and q-value to anomalies.

Revision ID: c1f8a37e2b64
Revises: b8c4f26d90a1

The suite runs sixteen detectors against every member, so some of what it flags
is what running thousands of tests over hundreds of people produces. These two
columns are what lets a reader tell the difference.

Both are nullable, and NULL carries meaning: it says no null model exists for
that detector, not that the finding passed. Only the timing-coincidence
detectors admit one.
"""

import sqlalchemy as sa
from alembic import op

revision = "c1f8a37e2b64"
down_revision = "b8c4f26d90a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("anomalies", sa.Column("p_value", sa.Float(), nullable=True))
    op.add_column("anomalies", sa.Column("q_value", sa.Float(), nullable=True))
    op.create_index("ix_anomalies_q_value", "anomalies", ["q_value"])


def downgrade() -> None:
    op.drop_index("ix_anomalies_q_value", table_name="anomalies")
    op.drop_column("anomalies", "q_value")
    op.drop_column("anomalies", "p_value")
