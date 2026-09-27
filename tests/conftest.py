"""Shared pytest fixtures for all Akad tests.

Design goals:
- Zero external dependencies (no real files, no real DB, no real network)
- Factories that produce minimal valid objects so tests only set what they care about
- Parquet file fixture for integration tests that need real storage reads
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from akad.models.contract import DataContract
from akad.models.result import ClauseResult, OverallStatus, ValidationResult

# ─── Contract factory ─────────────────────────────────────────────────────────

def make_contract(
    *,
    name: str = "test_contract",
    version: str = "1.0.0",
    fmt: str = "parquet",
    location: str = "/tmp/test.parquet",
    on_breach: str = "warn",
    schema_columns: list[dict] | None = None,
    freshness: dict | None = None,
    volume: dict | None = None,
    quality: list[dict] | None = None,
    business_rules: list[dict] | None = None,
    notifications: dict | None = None,
    consumers: list[dict] | None = None,
) -> DataContract:
    """Build a minimal valid DataContract for testing."""
    raw: dict[str, Any] = {
        "apiVersion": "datacontract/v1",
        "kind": "DataContract",
        "metadata": {
            "name": name,
            "version": version,
            "owner": {"team": "Test Team", "email": "test@example.com"},
        },
        "dataset": {"format": fmt, "location": location},
        "on_breach": on_breach,
    }
    if schema_columns is not None:
        raw["schema"] = {"columns": schema_columns}
    if freshness is not None:
        raw["freshness"] = freshness
    if volume is not None:
        raw["volume"] = volume
    if quality is not None:
        raw["quality"] = quality
    if business_rules is not None:
        raw["business_rules"] = business_rules
    if notifications is not None:
        raw["notifications"] = notifications
    if consumers is not None:
        raw["consumers"] = consumers
    return DataContract.model_validate(raw)


# ─── DataFrame factory ────────────────────────────────────────────────────────

def make_transactions_df(
    n: int = 5,
    *,
    include_nulls: bool = False,
    bad_currency: bool = False,
    bad_status: bool = False,
) -> pd.DataFrame:
    """Return a clean transactions DataFrame. Flags inject specific breaches."""
    ids      = [f"TXN{i:04d}" for i in range(n)]
    amounts  = [float(100 + i * 10) for i in range(n)]
    currency = ["MYR"] * n
    status   = ["COMPLETED"] * n

    if bad_currency:
        currency[0] = "JPY"
    if bad_status:
        status[0] = "UNKNOWN"

    df = pd.DataFrame({
        "transaction_id": ids,
        "amount":         amounts,
        "currency_code":  currency,
        "status":         status,
    })

    if include_nulls:
        df.at[0, "transaction_id"] = None

    return df


# ─── Parquet file fixture ─────────────────────────────────────────────────────

@pytest.fixture()
def tmp_parquet(tmp_path: Path) -> Path:
    """Write a clean transactions Parquet file and return its path."""
    df   = make_transactions_df(n=10)
    path = tmp_path / "transactions.parquet"
    table = pa.Table.from_pandas(df)
    pq.write_table(table, str(path))
    return path


# ─── Contract fixtures ────────────────────────────────────────────────────────

@pytest.fixture()
def basic_schema_contract(tmp_parquet: Path) -> DataContract:
    """Contract with schema + volume pointing to the tmp_parquet fixture."""
    return make_contract(
        location=str(tmp_parquet),
        schema_columns=[
            {"name": "transaction_id", "type": "string", "nullable": False},
            {"name": "amount",         "type": "float",  "nullable": False},
            {"name": "currency_code",  "type": "string", "nullable": False,
             "allowed_values": ["MYR", "USD", "SGD"]},
            {"name": "status",         "type": "string", "nullable": True,
             "allowed_values": ["COMPLETED", "PENDING", "FAILED"]},
        ],
        volume={"min_rows": 1, "max_rows": 1000},
    )


@pytest.fixture()
def fixture_contract_path() -> Path:
    """Return path to the valid_transactions.yaml fixture contract."""
    return Path(__file__).parent / "fixtures" / "contracts" / "valid_transactions.yaml"


# ─── ValidationResult factory ─────────────────────────────────────────────────

def make_validation_result(
    *,
    status: OverallStatus = OverallStatus.COMPLIANT,
    clauses: list[ClauseResult] | None = None,
    contract_name: str = "test_contract",
) -> ValidationResult:
    return ValidationResult(
        contract_name=contract_name,
        contract_version="1.0.0",
        dataset_location="/tmp/test.parquet",
        validated_at=datetime.now(UTC),
        overall_status=status,
        clause_results=clauses or [],
        row_count=10,
    )


# ─── Mock notifier ────────────────────────────────────────────────────────────

class RecordingNotifier:
    """Notifier that records calls instead of sending anything. Use in tests."""

    def __init__(self):
        self.calls: list[tuple] = []

    def notify(self, contract: DataContract, result: ValidationResult) -> None:
        self.calls.append((contract, result))


@pytest.fixture()
def recording_notifier() -> RecordingNotifier:
    return RecordingNotifier()


# ─── Registry client (per-test file-based SQLite) ────────────────────────────

REGISTRY_TEST_TOKEN = "test-token"


@pytest.fixture()
def registry_client(tmp_path, monkeypatch):
    """FastAPI TestClient backed by a fresh SQLite database per test.

    Uses dependency_overrides so the API's get_db dependency uses the same
    test engine — no module reload tricks needed. The app's startup migrations
    run against that engine too, so every API test exercises the real schema
    migrations and never touches the default ./akad_registry.db. The registry
    accepts REGISTRY_TEST_TOKEN, and the client sends it on every request.
    """
    monkeypatch.setenv("AKAD_API_TOKENS", REGISTRY_TEST_TOKEN)
    monkeypatch.delenv("AKAD_API_TOKEN", raising=False)
    monkeypatch.delenv("AKAD_REGISTRY_READS_REQUIRE_AUTH", raising=False)
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from registry.database import get_db, run_migrations
    from registry.main import app

    db_file   = tmp_path / "test_registry.db"
    test_engine  = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    TestSession = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)  # noqa: N806 — SQLAlchemy's own convention for a session factory
    monkeypatch.setattr("registry.main.run_migrations", lambda: run_migrations(test_engine))

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, headers={"Authorization": f"Bearer {REGISTRY_TEST_TOKEN}"}) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def akad_registry_client(registry_client):
    """A RegistryClient wired to the test FastAPI app.

    TestClient IS an httpx.Client subclass — injecting it directly lets
    RegistryClient make real SDK calls without a running server.
    """
    from akad.registry_client import RegistryClient
    return RegistryClient("http://testserver", _http_client=registry_client)
