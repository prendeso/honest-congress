"""Record whether a contract action modifies an existing contract

`detect_contract_front_runs` treated every positive-dollar USASpending action
as a contract being "awarded". Most actions are not: they are modifications --
incremental funding, an option exercised, a supplemental agreement -- on a
contract signed years earlier. Measured against USASpending for all 437 live
contract front-run findings: 183 pointed at a modification. One was Bell
Textron's FLRAA contract, signed 2022-12-05, published as "awarded" on
2026-04-02 because $336M of funding was added to it that day.

USASpending's `Mod` field says which is which: "0" (or blank, or any run of
zeros) is the base award, anything else -- "P00086", "A00012" -- modifies it.
The ingester has always requested the field and kept it only inside
`external_id` (`award id|mod|date|amount`). This stores it as a column the
detector can filter on, backfilled from that key.

NULL means the source did not say: an absent figure is not evidence either way,
the same rule `award_action_criteria` applies to a missing amount.

Revision ID: e2c8f1a4d7b3
Revises: d1a7c39e50b8
"""

import sqlalchemy as sa

from alembic import op

revision = "e2c8f1a4d7b3"
down_revision = "d1a7c39e50b8"
branch_labels = None
depends_on = None


def _is_modification(external_id):
    # Kept in step with `src.ingestion.usaspending.is_modification`. A migration
    # does not import application code, which is free to change after it runs.
    parts = (external_id or "").split("|")
    if len(parts) < 2:
        return None
    mod = parts[1].strip()
    if not mod:
        return None
    return mod.strip("0") != ""


def upgrade() -> None:
    op.add_column(
        "government_contracts",
        sa.Column("is_modification", sa.Boolean(), nullable=True),
    )

    bind = op.get_bind()
    contracts = sa.table(
        "government_contracts",
        sa.column("id", sa.Integer),
        sa.column("external_id", sa.String),
        sa.column("is_modification", sa.Boolean),
    )
    rows = bind.execute(sa.select(contracts.c.id, contracts.c.external_id)).fetchall()
    for flag in (True, False):
        ids = [row.id for row in rows if _is_modification(row.external_id) is flag]
        for start in range(0, len(ids), 500):
            bind.execute(
                contracts.update()
                .where(contracts.c.id.in_(ids[start : start + 500]))
                .values(is_modification=flag)
            )


def downgrade() -> None:
    op.drop_column("government_contracts", "is_modification")
