"""Add source-native external IDs to the Tier-2 trigger tables.

Revision ID: f7b3c2a51d90
Revises: e5f1a93c284d

The three Tier-2 tables had no unique constraint, so every ingester deduplicated
on a natural key -- (member, ticker, date, amount) for donations, (ticker, date,
description) for contracts. Real FEC data breaks that: Boeing's PAC contributed
$5,000 to the same committee twice on 2024-12-31 (primary and general), and the
natural key merges them into one donation, understating the relationship.

Each source publishes its own identifier for the record -- FEC `sub_id`, LDA
`filing_uuid`, USASpending award ID -- which is both unique and stable across
reruns. The unique index is on (source, external_id) rather than external_id
alone, because the identifiers come from different namespaces. NULLs stay
allowed: rows ingested before this migration, and any future source without an
identifier, are not blocked.
"""

import sqlalchemy as sa
from alembic import op

revision = "f7b3c2a51d90"
down_revision = "e5f1a93c284d"
branch_labels = None
depends_on = None


_TABLES = (
    ("campaign_donations", "ix_donations_source_external"),
    ("lobbying_disclosures", "ix_lobbying_source_external"),
    ("government_contracts", "ix_contracts_source_external"),
)


def upgrade() -> None:
    for table, index_name in _TABLES:
        op.add_column(table, sa.Column("external_id", sa.String(length=100), nullable=True))
        op.create_index(index_name, table, ["source", "external_id"], unique=True)


def downgrade() -> None:
    for table, index_name in _TABLES:
        op.drop_index(index_name, table_name=table)
        op.drop_column(table, "external_id")
