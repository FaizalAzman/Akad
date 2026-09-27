from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite:///./akad_registry.db",  # fallback for local dev & tests
)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


MIGRATIONS_DIR = Path(__file__).parent / "migrations"
BASELINE_REVISION = "0001"
MIGRATION_LOCK_KEY = 0x616B6164  # "akad": PostgreSQL advisory lock id for migrations


def run_migrations(target: Engine | None = None) -> None:
    """Bring the database schema up to date. Runs on every registry startup.

    A database created by registry versions before 1.5 (via create_all, with
    no alembic_version table) is stamped at the baseline revision first, so
    it is upgraded in place rather than recreated.

    On PostgreSQL an advisory lock serialises this across processes, so
    several workers or replicas starting at once don't race to create the
    same tables. SQLite is for single-process local development only.
    """
    from alembic import command
    from alembic.config import Config

    target = target or engine
    with target.begin() as conn:
        if conn.dialect.name == "postgresql":
            # Held until this transaction commits; later starters then see the finished schema.
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "contracts" in tables and "alembic_version" not in tables:
            command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")
