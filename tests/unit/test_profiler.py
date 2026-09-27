"""Tests for akad.profiler — dataset profiling and starter contract generation.

Uses purpose-built DataFrames (not the shared make_transactions_df fixture)
so the allowed_values cardinality heuristic is exercised meaningfully —
with only 10 rows, an all-unique ID column would itself fall under the
default cardinality cap and need the nunique < len(s) "values must repeat"
guard to be correctly excluded.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
import yaml

from akad.engine import validate_dataframe
from akad.models.contract import ColumnType, DataContract
from akad.models.result import OverallStatus
from akad.profiler import (
    _infer_column_type,
    contract_to_yaml_dict,
    generate_contract,
    profile_dataframe,
)


def _wide_df(n: int = 30) -> pd.DataFrame:
    """30 rows: an all-unique id column, a 3-value category column, a
    numeric column, a column with a null AND duplicates (no rule should
    apply), and a column with a null but otherwise-unique values (only
    the duplicate rule should apply)."""
    return pd.DataFrame({
        "order_id":  [f"ORD{i:04d}" for i in range(n)],
        "status":    (["PAID", "PENDING", "REFUNDED"] * 10)[:n],
        "amount":    [round(10.0 + i * 1.5, 2) for i in range(n)],
        "note":      ["ok"] * (n - 1) + [None],
        "serial":    [f"SN{i:04d}" for i in range(n - 1)] + [None],
    })


class TestInferColumnType:
    def test_integer_dtype(self):
        assert _infer_column_type(pd.Series([1, 2, 3], dtype="int64")) == ColumnType.INTEGER

    def test_float_with_nulls_promoted_but_whole_numbers_is_integer(self):
        s = pd.array([1.0, 2.0, None], dtype="float64")
        assert _infer_column_type(pd.Series(s)) == ColumnType.INTEGER

    def test_float_with_fractional_values_is_float(self):
        assert _infer_column_type(pd.Series([1.5, 2.0])) == ColumnType.FLOAT

    def test_all_null_float_column_is_float_not_integer(self):
        s = pd.Series([None, None], dtype="float64")
        assert _infer_column_type(s) == ColumnType.FLOAT

    def test_bool_dtype(self):
        assert _infer_column_type(pd.Series([True, False])) == ColumnType.BOOLEAN

    def test_datetime_dtype(self):
        s = pd.to_datetime(["2024-01-01", "2024-01-02"])
        assert _infer_column_type(pd.Series(s)) == ColumnType.TIMESTAMP

    def test_object_dtype_is_string(self):
        assert _infer_column_type(pd.Series(["a", "b"])) == ColumnType.STRING

    def test_decimal_objects_are_decimal(self):
        s = pd.Series([Decimal("1.10"), None], dtype=object)
        assert _infer_column_type(s) == ColumnType.DECIMAL

    def test_date_objects_are_date(self):
        s = pd.Series([date(2026, 1, 1), None], dtype=object)
        assert _infer_column_type(s) == ColumnType.DATE

    def test_all_null_object_column_is_string(self):
        s = pd.Series([None, None], dtype=object)
        assert _infer_column_type(s) == ColumnType.STRING


class TestAllowedValuesInference:
    def test_repeated_low_cardinality_string_gets_allowed_values(self):
        profile = profile_dataframe(_wide_df())
        status_col = next(c for c in profile["schema"]["columns"] if c["name"] == "status")
        assert status_col["allowed_values"] == ["PAID", "PENDING", "REFUNDED"]

    def test_all_unique_string_column_does_not_get_allowed_values(self):
        profile = profile_dataframe(_wide_df())
        id_col = next(c for c in profile["schema"]["columns"] if c["name"] == "order_id")
        assert "allowed_values" not in id_col

    def test_cardinality_above_cap_excluded(self):
        df = pd.DataFrame({"tag": (["x", "y"] * 5) + [f"unique{i}" for i in range(25)]})
        profile = profile_dataframe(df, max_allowed_values_cardinality=5)
        tag_col = profile["schema"]["columns"][0]
        assert "allowed_values" not in tag_col

    def test_numeric_column_never_gets_allowed_values(self):
        profile = profile_dataframe(pd.DataFrame({"n": [1, 1, 1, 2, 2]}))
        assert "allowed_values" not in profile["schema"]["columns"][0]


class TestNullableInference:
    def test_column_with_no_nulls_is_not_nullable(self):
        profile = profile_dataframe(_wide_df())
        order_id = next(c for c in profile["schema"]["columns"] if c["name"] == "order_id")
        assert order_id["nullable"] is False

    def test_column_with_a_null_is_nullable(self):
        profile = profile_dataframe(_wide_df())
        note = next(c for c in profile["schema"]["columns"] if c["name"] == "note")
        assert note["nullable"] is True


class TestQualityRuleInference:
    def test_key_like_column_gets_both_null_and_duplicate_rules(self):
        profile = profile_dataframe(_wide_df())
        rule = next(r for r in profile["quality"] if r["column"] == "order_id")
        assert rule["max_null_percentage"] == 0.0
        assert rule["max_duplicate_percentage"] == 0.0

    def test_repeated_category_column_gets_null_rule_but_not_duplicate_rule(self):
        profile = profile_dataframe(_wide_df())
        rule = next(r for r in profile["quality"] if r["column"] == "status")
        assert rule["max_null_percentage"] == 0.0
        assert "max_duplicate_percentage" not in rule

    def test_numeric_column_gets_min_max(self):
        profile = profile_dataframe(_wide_df())
        rule = next(r for r in profile["quality"] if r["column"] == "amount")
        assert rule["min_value"] == pytest.approx(10.0)
        assert rule["max_value"] == pytest.approx(10.0 + 29 * 1.5)

    def test_bool_column_does_not_get_min_max(self):
        profile = profile_dataframe(pd.DataFrame({"flag": [True, False, True]}))
        rules = [r for r in profile["quality"] if r["column"] == "flag"]
        assert all("min_value" not in r for r in rules)

    def test_column_with_nulls_and_no_dups_gets_only_duplicate_rule(self):
        profile = profile_dataframe(_wide_df())
        rule = next(r for r in profile["quality"] if r["column"] == "serial")
        assert "max_null_percentage" not in rule
        assert rule["max_duplicate_percentage"] == 0.0

    def test_column_with_nulls_and_duplicates_gets_no_quality_rule(self):
        profile = profile_dataframe(_wide_df())
        assert not any(r["column"] == "note" for r in profile["quality"])

    def test_empty_dataframe_quality_is_empty(self):
        profile = profile_dataframe(pd.DataFrame({"x": pd.Series([], dtype="object")}))
        assert profile["quality"] == []


class TestVolumeInference:
    def test_bounds_bracket_observed_row_count(self):
        profile = profile_dataframe(_wide_df(30))
        assert profile["volume"]["min_rows"] == 15
        assert profile["volume"]["max_rows"] == 60

    def test_empty_dataframe_has_no_volume(self):
        profile = profile_dataframe(pd.DataFrame({"x": pd.Series([], dtype="object")}))
        assert "volume" not in profile


class TestGenerateContract:
    def test_builds_valid_contract(self):
        contract = generate_contract(
            _wide_df(),
            name="orders",
            dataset_format="parquet",
            owner_team="Data Eng",
            owner_email="data@example.com",
            location="/data/orders.parquet",
        )
        assert isinstance(contract, DataContract)
        assert contract.metadata.name == "orders"
        assert contract.on_breach == "warn"
        assert contract.dataset.location == "/data/orders.parquet"
        assert contract.schema_ is not None
        assert len(contract.schema_.columns) == 5

    def test_sql_dataset_fields(self):
        contract = generate_contract(
            _wide_df(),
            name="orders",
            dataset_format="sql",
            owner_team="Data Eng",
            owner_email="data@example.com",
            table_name="orders",
            connection_string="postgresql://x/y",
        )
        assert contract.dataset.table_name == "orders"
        assert contract.dataset.connection_string == "postgresql://x/y"
        assert contract.dataset.location is None


class TestContractToYamlDict:
    def test_round_trips_through_yaml(self):
        contract = generate_contract(
            _wide_df(),
            name="orders",
            dataset_format="parquet",
            owner_team="Data Eng",
            owner_email="data@example.com",
            location="/data/orders.parquet",
        )
        text = yaml.dump(contract_to_yaml_dict(contract), sort_keys=False)
        reloaded = DataContract.model_validate(yaml.safe_load(text))
        assert reloaded.metadata.name == contract.metadata.name
        assert len(reloaded.schema_.columns) == len(contract.schema_.columns)
        assert reloaded.quality == contract.quality

    def test_omits_none_fields(self):
        contract = generate_contract(
            _wide_df(),
            name="orders",
            dataset_format="parquet",
            owner_team="Data Eng",
            owner_email="data@example.com",
            location="/data/orders.parquet",
        )
        d = contract_to_yaml_dict(contract)
        assert "table_name" not in d["dataset"]
        assert "connection_string" not in d["dataset"]


class TestSelfConsistency:
    """The whole point of `akad infer`: validating the same data against
    its own inferred contract must come back COMPLIANT."""

    def test_inferred_contract_validates_clean_against_its_own_data(self):
        df = _wide_df()
        contract = generate_contract(
            df,
            name="orders",
            dataset_format="parquet",
            owner_team="Data Eng",
            owner_email="data@example.com",
            location="/data/orders.parquet",
        )
        result = validate_dataframe(df, contract)
        assert result.overall_status == OverallStatus.COMPLIANT
        assert result.failed_clauses == []
