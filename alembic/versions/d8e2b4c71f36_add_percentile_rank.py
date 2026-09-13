"""Add Anomaly.percentile_rank.

Every threshold in the detector suite is asserted rather than derived from the
data, which makes "exceeded threshold 100" hard to defend. Ranking a finding
against others of its own type turns the same number into a statement that
stands on its own. Null means the population was too small to rank against.

Revision ID: d8e2b4c71f36
Revises: c3a7f1d92b04
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d8e2b4c71f36"
down_revision: Union[str, None] = "c3a7f1d92b04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("anomalies", sa.Column("percentile_rank", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("anomalies", "percentile_rank")
