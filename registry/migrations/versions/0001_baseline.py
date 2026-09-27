"""Baseline: the schema registry versions up to 1.4 created with create_all.

Revision ID: 0001
Revises:
Create Date: 2026-09-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contracts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_contracts_id", "contracts", ["id"])
    op.create_index("ix_contracts_name", "contracts", ["name"])

    op.create_table(
        "validation_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contract_name", sa.String(255), nullable=False),
        sa.Column("contract_version", sa.String(50), nullable=False),
        sa.Column("dataset_location", sa.Text(), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("overall_status", sa.String(20), nullable=False),
        sa.Column("row_count", sa.Integer()),
        sa.Column("clause_results", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text()),
    )
    op.create_index("ix_validation_results_id", "validation_results", ["id"])
    op.create_index("ix_validation_results_contract_name", "validation_results", ["contract_name"])


def downgrade() -> None:
    op.drop_table("validation_results")
    op.drop_table("contracts")
