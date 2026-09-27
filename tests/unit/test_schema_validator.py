from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pyarrow as pa
import pytest

from akad.models.result import ClauseStatus
from akad.validators.schema_validator import SchemaValidator
from tests.conftest import make_contract, make_transactions_df


class TestColumnExists:
    def test_pass_when_all_columns_present(self):
        df = make_transactions_df(5)
        contract = make_contract(schema_columns=[
            {"name": "transaction_id", "type": "string"},
            {"name": "amount",         "type": "float"},
        ])
        results = SchemaValidator().validate(df, contract, None)
        fails = [r for r in results if r.status == ClauseStatus.FAIL]
        assert not fails

    def test_fail_when_column_missing(self):
        df = pd.DataFrame({"id": [1, 2]})
        contract = make_contract(schema_columns=[
            {"name": "id",   "type": "integer"},
            {"name": "name", "type": "string"},
        ])
        results = SchemaValidator().validate(df, contract, None)
        fail = [r for r in results if r.clause_type == "schema.column_exists"]
        assert len(fail) == 1
        assert fail[0].clause_target == "name"
        assert fail[0].status == ClauseStatus.FAIL

    def test_no_results_when_no_schema(self):
        df = make_transactions_df(3)
        contract = make_contract()  # no schema_columns
        results = SchemaValidator().validate(df, contract, None)
        assert results == []


class TestColumnType:
    def test_pass_on_correct_integer_type(self):
        df = pd.DataFrame({"count": pd.array([1, 2, 3], dtype="int64")})
        contract = make_contract(schema_columns=[{"name": "count", "type": "integer"}])
        results = SchemaValidator().validate(df, contract, None)
        type_results = [r for r in results if r.clause_type == "schema.column_type"]
        assert all(r.status == ClauseStatus.PASS for r in type_results)

    def test_fail_on_wrong_type(self):
        df = pd.DataFrame({"count": ["a", "b"]})
        contract = make_contract(schema_columns=[{"name": "count", "type": "integer"}])
        results = SchemaValidator().validate(df, contract, None)
        fail = [r for r in results if r.clause_type == "schema.column_type"]
        assert fail[0].status == ClauseStatus.FAIL

    def test_pass_on_integer_column_promoted_to_float_by_nulls(self):
        # pandas promotes int64 -> float64 as soon as a column has a null
        df = pd.DataFrame({"count": pd.array([1.0, 2.0, None], dtype="float64")})
        contract = make_contract(schema_columns=[{"name": "count", "type": "integer"}])
        results = SchemaValidator().validate(df, contract, None)
        type_results = [r for r in results if r.clause_type == "schema.column_type"]
        assert type_results[0].status == ClauseStatus.PASS

    def test_fail_on_float_column_with_real_fractional_values(self):
        df = pd.DataFrame({"count": pd.array([1.5, 2.0], dtype="float64")})
        contract = make_contract(schema_columns=[{"name": "count", "type": "integer"}])
        results = SchemaValidator().validate(df, contract, None)
        type_results = [r for r in results if r.clause_type == "schema.column_type"]
        assert type_results[0].status == ClauseStatus.FAIL


class TestNullability:
    def test_pass_when_non_nullable_has_no_nulls(self):
        df = pd.DataFrame({"id": ["A", "B", "C"]})
        contract = make_contract(schema_columns=[{"name": "id", "type": "string", "nullable": False}])
        results = SchemaValidator().validate(df, contract, None)
        nullable_results = [r for r in results if r.clause_type == "schema.column_nullable"]
        assert all(r.status == ClauseStatus.PASS for r in nullable_results)

    def test_fail_when_non_nullable_has_nulls(self):
        df = pd.DataFrame({"id": ["A", None, "C"]})
        contract = make_contract(schema_columns=[{"name": "id", "type": "string", "nullable": False}])
        results = SchemaValidator().validate(df, contract, None)
        fail = [r for r in results if r.clause_type == "schema.column_nullable"]
        assert len(fail) == 1
        assert fail[0].status == ClauseStatus.FAIL

    def test_nullable_true_skips_null_check(self):
        df = pd.DataFrame({"note": [None, None]})
        contract = make_contract(schema_columns=[{"name": "note", "type": "string", "nullable": True}])
        results = SchemaValidator().validate(df, contract, None)
        nullable_results = [r for r in results if r.clause_type == "schema.column_nullable"]
        assert not nullable_results  # no check performed


class TestAllowedValues:
    def test_pass_when_all_values_allowed(self):
        df = make_transactions_df(3)
        contract = make_contract(schema_columns=[
            {"name": "currency_code", "type": "string", "allowed_values": ["MYR", "USD", "SGD"]},
        ])
        results = SchemaValidator().validate(df, contract, None)
        av = [r for r in results if r.clause_type == "schema.allowed_values"]
        assert av[0].status == ClauseStatus.PASS

    def test_fail_when_unexpected_value(self):
        df = make_transactions_df(3, bad_currency=True)
        contract = make_contract(schema_columns=[
            {"name": "currency_code", "type": "string", "allowed_values": ["MYR", "USD", "SGD"]},
        ])
        results = SchemaValidator().validate(df, contract, None)
        av = [r for r in results if r.clause_type == "schema.allowed_values"]
        assert av[0].status == ClauseStatus.FAIL
        assert "JPY" in str(av[0].observed)


class TestNoExtraColumns:
    def test_pass_when_no_extra_columns(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        contract = make_contract(schema_columns=[
            {"name": "a", "type": "integer"},
            {"name": "b", "type": "integer"},
        ])
        contract.schema_.enforce_no_extra_columns = True
        results = SchemaValidator().validate(df, contract, None)
        extra = [r for r in results if r.clause_type == "schema.no_extra_columns"]
        assert extra[0].status == ClauseStatus.PASS

    def test_fail_when_extra_column_present(self):
        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        contract = make_contract(schema_columns=[
            {"name": "a", "type": "integer"},
            {"name": "b", "type": "integer"},
        ])
        contract.schema_.enforce_no_extra_columns = True
        results = SchemaValidator().validate(df, contract, None)
        extra = [r for r in results if r.clause_type == "schema.no_extra_columns"]
        assert extra[0].status == ClauseStatus.FAIL
        assert "c" in str(extra[0].observed)


def _type_status(series: pd.Series, declared: str) -> ClauseStatus:
    df = pd.DataFrame({"col": series})
    contract = make_contract(schema_columns=[{"name": "col", "type": declared}])
    results = SchemaValidator().validate(df, contract, None)
    return next(r.status for r in results if r.clause_type == "schema.column_type")


class TestParquetDecimalAndDateTypes:
    """pyarrow's to_pandas() turns decimal128 and date32 into object columns
    of Decimal / date values — these must still match decimal / date."""

    def test_decimal_objects_match_decimal(self):
        s = pd.Series([Decimal("10.50"), None, Decimal("3.00")], dtype=object)
        assert _type_status(s, "decimal") == ClauseStatus.PASS

    def test_date_objects_match_date(self):
        s = pd.Series([date(2026, 1, 1), None], dtype=object)
        assert _type_status(s, "date") == ClauseStatus.PASS

    def test_arrow_backed_decimal_matches_decimal(self):
        dtype = pd.ArrowDtype(pa.decimal128(10, 2))
        s = pd.Series(pa.array([Decimal("1.10"), None], pa.decimal128(10, 2)), dtype=dtype)
        assert _type_status(s, "decimal") == ClauseStatus.PASS

    def test_float_still_matches_decimal(self):
        assert _type_status(pd.Series([1.5, 2.25]), "decimal") == ClauseStatus.PASS

    @pytest.mark.parametrize(("values", "declared"), [
        (["10.50", "3.00"], "decimal"),                 # numeric-looking strings
        ([date(2026, 1, 1)], "decimal"),                # dates aren't decimals
        (["2026-01-01"], "date"),                       # date-looking strings
        ([Decimal("1.0")], "date"),                     # decimals aren't dates
        ([Decimal("1.0"), "oops"], "decimal"),          # one stray value fails the column
    ])
    def test_other_object_values_do_not_match(self, values, declared):
        assert _type_status(pd.Series(values, dtype=object), declared) == ClauseStatus.FAIL
