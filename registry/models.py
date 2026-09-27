from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from registry.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


class ContractRecord(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        # A published version is immutable: one row per (name, version).
        Index("uq_contracts_name_version", "name", "version", unique=True),
        # At most one current version per contract, enforced by the database so
        # two concurrent publishes can't both end up current. Partial indexes
        # exist only on PostgreSQL and SQLite, the two supported backends.
        Index(
            "uq_contracts_one_current_per_name", "name", unique=True,
            postgresql_where=text("is_current"), sqlite_where=text("is_current = 1"),
        ).ddl_if(dialect=("postgresql", "sqlite")),
    )

    id:           Mapped[int]      = mapped_column(Integer, primary_key=True, index=True)
    name:         Mapped[str]      = mapped_column(String(255), nullable=False, index=True)
    version:      Mapped[str]      = mapped_column(String(50), nullable=False)
    content:      Mapped[str]      = mapped_column(Text, nullable=False)   # JSON string
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    is_current:   Mapped[bool]     = mapped_column(Boolean, default=True, nullable=False)


class ValidationResultRecord(Base):
    __tablename__ = "validation_results"

    id:               Mapped[int]           = mapped_column(Integer, primary_key=True, index=True)
    contract_name:    Mapped[str]           = mapped_column(String(255), nullable=False, index=True)
    contract_version: Mapped[str]           = mapped_column(String(50), nullable=False)
    dataset_location: Mapped[str]           = mapped_column(Text, nullable=False)
    validated_at:     Mapped[datetime]      = mapped_column(DateTime(timezone=True), nullable=False)
    overall_status:   Mapped[str]           = mapped_column(String(20), nullable=False)
    row_count:        Mapped[int | None]    = mapped_column(Integer, nullable=True)
    clause_results:   Mapped[str]           = mapped_column(Text, nullable=False)   # JSON string
    error_message:    Mapped[str | None]    = mapped_column(Text, nullable=True)
