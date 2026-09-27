"""Make contract versions immutable and allow only one current version per name.

Earlier registries let a version be published twice. Those duplicates can't be
merged safely, since the rows may hold different content, so this migration
refuses to run until an operator resolves them. It never deletes data.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

contracts = sa.table(
    "contracts",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("version", sa.String),
    sa.column("published_at", sa.DateTime),
    sa.column("is_current", sa.Boolean),
)


def _refuse_if_duplicate_versions(conn: sa.Connection) -> None:
    duplicates = conn.execute(
        sa.select(contracts.c.name, contracts.c.version)
        .group_by(contracts.c.name, contracts.c.version)
        .having(sa.func.count() > 1)
    ).all()
    if not duplicates:
        return
    listing = []
    for name, version in duplicates:
        ids = conn.execute(
            sa.select(contracts.c.id)
            .where(contracts.c.name == name, contracts.c.version == version)
            .order_by(contracts.c.id)
        ).scalars().all()
        listing.append(f"{name} v{version} (row ids {', '.join(map(str, ids))})")
    raise RuntimeError(
        "Cannot make contract versions immutable: these versions were published more "
        f"than once: {'; '.join(listing)}. Decide which row of each is authoritative, "
        "delete the others from the contracts table, then restart the registry."
    )


def _one_current_per_name(conn: sa.Connection) -> None:
    """The latest-published row of each contract becomes its only current version,
    matching how the registry chose the current version before this migration."""
    rows = conn.execute(
        sa.select(contracts.c.id, contracts.c.name)
        .order_by(contracts.c.name, contracts.c.published_at, contracts.c.id)
    ).all()
    if not rows:
        return
    latest = {name: row_id for row_id, name in rows}
    conn.execute(contracts.update().values(
        is_current=sa.case((contracts.c.id.in_(list(latest.values())), sa.true()), else_=sa.false())
    ))


def upgrade() -> None:
    conn = op.get_bind()
    _refuse_if_duplicate_versions(conn)
    _one_current_per_name(conn)
    op.create_index("uq_contracts_name_version", "contracts", ["name", "version"], unique=True)
    if conn.dialect.name in ("postgresql", "sqlite"):
        op.create_index(
            "uq_contracts_one_current_per_name", "contracts", ["name"], unique=True,
            postgresql_where=sa.text("is_current"), sqlite_where=sa.text("is_current = 1"),
        )


def downgrade() -> None:
    if op.get_bind().dialect.name in ("postgresql", "sqlite"):
        op.drop_index("uq_contracts_one_current_per_name", table_name="contracts")
    op.drop_index("uq_contracts_name_version", table_name="contracts")
