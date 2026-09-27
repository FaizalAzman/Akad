"""Registry schema migrations: fresh databases, databases created by registry
versions before migrations existed, and the duplicate-version guard.

Runs against SQLite always, and also against PostgreSQL when
AKAD_TEST_POSTGRES_URL points at a disposable database (its public schema
is wiped before each test).
"""
from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from registry.database import MIGRATIONS_DIR, Base, run_migrations

POSTGRES_URL = os.environ.get("AKAD_TEST_POSTGRES_URL")


@pytest.fixture(params=[
    "sqlite",
    pytest.param("postgresql", marks=pytest.mark.skipif(not POSTGRES_URL, reason="AKAD_TEST_POSTGRES_URL not set")),
])
def engine(request, tmp_path):
    if request.param == "sqlite":
        yield create_engine(f"sqlite:///{tmp_path / 'registry.db'}")
        return
    engine = create_engine(POSTGRES_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    yield engine
    engine.dispose()


def _legacy_database(engine) -> None:
    """The schema registries up to 1.4 created with create_all: the baseline
    tables, with no alembic_version table and none of the new indexes."""
    with engine.begin() as conn:
        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001")
        conn.execute(text("DROP TABLE alembic_version"))


def _insert(engine, *rows: tuple[int, str, str, int]) -> None:
    with engine.begin() as conn:
        for row_id, name, version, current in rows:
            conn.execute(text(
                "INSERT INTO contracts (id, name, version, content, published_at, is_current) "
                "VALUES (:id, :name, :version, '{}', :published_at, :current)"
            ), {"id": row_id, "name": name, "version": version,
                "published_at": f"2026-01-0{row_id} 00:00:00", "current": bool(current)})


def _migrate_in_fresh_process(url: str) -> None:
    run_migrations(create_engine(url))


def _index_names(engine) -> set[str]:
    return {i["name"] for i in inspect(engine).get_indexes("contracts")}


class TestFreshDatabase:
    def test_creates_schema_matching_the_models(self, engine):
        run_migrations(engine)
        with engine.connect() as conn:
            assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"

    def test_is_idempotent(self, engine):
        run_migrations(engine)
        run_migrations(engine)
        assert {"uq_contracts_name_version", "uq_contracts_one_current_per_name"} <= _index_names(engine)


    def test_concurrent_startups_migrate_once(self, engine):
        """Several workers or replicas starting together must all come up.
        Uses separate processes, as real workers are (Alembic's op/context
        proxies are process-global, so threads wouldn't model this)."""
        if engine.dialect.name != "postgresql":
            pytest.skip("concurrent startup is only supported on PostgreSQL")
        url = engine.url.render_as_string(hide_password=False)
        with ProcessPoolExecutor(6, mp_context=multiprocessing.get_context("spawn")) as pool:
            list(pool.map(_migrate_in_fresh_process, [url] * 6, timeout=60))  # any failure re-raises here
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"


class TestLegacyDatabase:
    def test_is_upgraded_in_place_keeping_data(self, engine):
        _legacy_database(engine)
        # Inconsistent state an old registry could reach through a race: two current rows.
        _insert(engine, (1, "sales", "1.0.0", 1), (2, "sales", "1.1.0", 1), (3, "orders", "1.0.0", 1))

        run_migrations(engine)

        with engine.connect() as conn:
            rows = conn.execute(text("SELECT name, version, is_current FROM contracts ORDER BY id")).all()
        assert [(n, v, bool(c)) for n, v, c in rows] == [
            ("sales", "1.0.0", False), ("sales", "1.1.0", True), ("orders", "1.0.0", True),
        ]
        assert {"uq_contracts_name_version", "uq_contracts_one_current_per_name"} <= _index_names(engine)

    def test_duplicate_versions_stop_the_migration_without_deleting_anything(self, engine):
        _legacy_database(engine)
        _insert(engine, (1, "sales", "1.0.0", 0), (2, "sales", "1.0.0", 1))

        with pytest.raises(RuntimeError, match=r"sales v1\.0\.0 \(row ids 1, 2\)"):
            run_migrations(engine)

        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM contracts")).scalar() == 2
