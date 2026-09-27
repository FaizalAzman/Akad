"""Integration tests for the validation engine.

These tests use validate_dataframe() which skips real storage reads,
and test_engine_with_parquet uses tmp_parquet fixture for the full read path.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from akad.engine import validate, validate_dataframe
from akad.models.result import OverallStatus
from akad.profiler import generate_contract
from tests.conftest import make_contract, make_transactions_df


class TestValidateDataframe:
    def test_compliant_result_on_clean_data(self):
        df = make_transactions_df(10)
        contract = make_contract(
            schema_columns=[
                {"name": "transaction_id", "type": "string", "nullable": False},
                {"name": "amount",         "type": "float",  "nullable": False},
                {"name": "currency_code",  "type": "string", "nullable": False,
                 "allowed_values": ["MYR", "USD", "SGD"]},
            ],
            volume={"min_rows": 1, "max_rows": 100},
            quality=[
                {"column": "transaction_id", "max_null_percentage": 0.0},
                {"column": "amount",         "min_value": 0.01},
            ],
        )
        result = validate_dataframe(df, contract)
        assert result.overall_status == OverallStatus.COMPLIANT
        assert result.row_count == 10
        assert not result.failed_clauses

    def test_breach_on_missing_column(self):
        df = pd.DataFrame({"amount": [10.0, 20.0]})
        contract = make_contract(schema_columns=[
            {"name": "amount",         "type": "float"},
            {"name": "transaction_id", "type": "string"},
        ])
        result = validate_dataframe(df, contract)
        assert result.overall_status == OverallStatus.BREACH
        assert result.is_breach
        missing = [c for c in result.failed_clauses if c.clause_type == "schema.column_exists"]
        assert len(missing) == 1

    def test_breach_on_volume_too_low(self):
        df = make_transactions_df(3)
        contract = make_contract(volume={"min_rows": 100})
        result = validate_dataframe(df, contract)
        assert result.overall_status == OverallStatus.BREACH
        vol_fail = [c for c in result.failed_clauses if c.clause_type == "volume.min_rows"]
        assert len(vol_fail) == 1

    def test_breach_on_bad_allowed_values(self):
        df = make_transactions_df(5, bad_currency=True)
        contract = make_contract(schema_columns=[
            {"name": "currency_code", "type": "string", "allowed_values": ["MYR", "USD", "SGD"]},
        ])
        result = validate_dataframe(df, contract)
        assert result.overall_status == OverallStatus.BREACH

    def test_compliant_on_fail_mode_does_not_raise(self):
        df = make_transactions_df(5)
        contract = make_contract(on_breach="fail")
        result = validate_dataframe(df, contract)
        assert result.overall_status == OverallStatus.COMPLIANT

    def test_multiple_breaches_all_reported(self):
        df = pd.DataFrame({"amount": [-999.0]})
        contract = make_contract(
            schema_columns=[
                {"name": "amount",   "type": "float",  "nullable": False},
                {"name": "missing1", "type": "string"},
                {"name": "missing2", "type": "integer"},
            ],
            quality=[{"column": "amount", "min_value": 0.01}],
        )
        result = validate_dataframe(df, contract)
        assert len(result.failed_clauses) >= 3


class TestValidateWithParquetFile:
    def test_validate_reads_parquet_file(self, tmp_parquet: Path):
        contract = make_contract(
            location=str(tmp_parquet),
            schema_columns=[
                {"name": "transaction_id", "type": "string"},
                {"name": "amount",         "type": "float"},
            ],
            volume={"min_rows": 1},
        )
        result = validate(contract)
        assert result.overall_status == OverallStatus.COMPLIANT
        assert result.row_count == 10

    def test_decimal_and_date_columns_from_real_parquet(self, tmp_path: Path):
        """Regression: decimal128/date32 read back as object columns and used
        to fail every decimal/date type check."""
        path = tmp_path / "financing.parquet"
        pq.write_table(pa.table({
            "outstanding": pa.array([Decimal("1500.00"), Decimal("250.75"), None], pa.decimal128(18, 2)),
            "as_of_date":  pa.array([date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)], pa.date32()),
            "booked_at":   pa.array([1, 2, 3], pa.timestamp("us")),
        }), path)
        contract = make_contract(
            location=str(path),
            schema_columns=[
                {"name": "outstanding", "type": "decimal"},
                {"name": "as_of_date",  "type": "date", "nullable": False},
                {"name": "booked_at",   "type": "timestamp"},
            ],
            quality=[{"column": "outstanding", "min_value": 0}],
        )
        result = validate(contract)
        assert result.overall_status == OverallStatus.COMPLIANT, result.failed_clauses

    def test_inferred_contract_validates_its_own_parquet(self, tmp_path: Path):
        """`akad infer` output must be compliant against the data it was inferred from."""
        path = tmp_path / "financing.parquet"
        table = pa.table({
            "account_id":  pa.array([f"ACC{i}" for i in range(6)]),
            "outstanding": pa.array([Decimal(f"{i}.50") for i in range(6)], pa.decimal128(18, 2)),
            "as_of_date":  pa.array([date(2026, 9, i + 1) for i in range(6)], pa.date32()),
        })
        pq.write_table(table, path)
        contract = generate_contract(table.to_pandas(), name="financing", dataset_format="parquet",
                                     owner_team="t", owner_email="t@example.com", location=str(path))
        types = {c.name: c.type.value for c in contract.schema_.columns}
        assert types == {"account_id": "string", "outstanding": "decimal", "as_of_date": "date"}
        assert validate(contract).overall_status == OverallStatus.COMPLIANT

    def test_validate_returns_error_on_bad_path(self):
        contract = make_contract(location="/nonexistent/path/data.parquet")
        result = validate(contract)
        assert result.overall_status == OverallStatus.ERROR
        assert result.error_message is not None
