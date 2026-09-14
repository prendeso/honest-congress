"""Re-key government_contracts on the award ACTION, not the award.

Revision ID: c1f70a2b93de
Revises: b9d3e07f4a15

`external_id` held USASpending's `internal_id`, which identifies the CONTRACT.
Contract W31P4Q24C0022 comes back from the live API as nine transaction rows
sharing that one id, with six distinct action dates and nine distinct amounts;
over 500 live rows the id had 437 distinct values. The rows discarded by that
key differ in ACTION DATE, which is the only field `detect_contract_front_runs`
reads.

`src/ingestion/usaspending.py` now keys on the contract, the modification, the
date and the amount. Rows already stored under the old key would never match the
new one, so a rerun would insert the same awards a second time under a different
`external_id` -- the unique index is on (source, external_id) and would not stop
it, and the detector would then count every award twice.

So the old rows go. This is safe in a way that deleting is usually not:
`government_contracts` is a cache of a public API, holds nothing this project
computed, and is rebuilt in full by the `ingest-contracts` step of the very next
run -- which takes about a second per traded company. Keeping stale rows keyed a
way nothing will ever produce again is the option with the real cost.

Scoped to `source = 'usaspending'` so that a row from any other feed, now or
later, is untouched.

The downgrade does not restore them, and says so rather than pretending: the old
`internal_id` is not recoverable from the composite key, and the previous
revision's ingester will refill the table on its next run anyway.
"""

import sqlalchemy as sa

from alembic import op

revision = "c1f70a2b93de"
down_revision = "b9d3e07f4a15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM government_contracts WHERE source = 'usaspending'"))


def downgrade() -> None:
    # Nothing to put back. The rows were a cache and the key they were stored
    # under cannot be reconstructed; `cli ingest-contracts` rebuilds the table.
    pass
