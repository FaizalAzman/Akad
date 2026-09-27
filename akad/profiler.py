"""Profile a dataset and scaffold a starter DataContract.

`akad infer` (see akad.cli) wraps this module for command-line use. The
output is a STARTING POINT — every inferred rule reflects only what the
data looked like at profiling time, not the business rules it's supposed
to follow. Review and tighten before relying on it in CI or production.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd

from akad.models.contract import ColumnType, DataContract
from akad.validators.schema_validator import is_integer_like, object_values_are

DEFAULT_MAX_ALLOWED_VALUES_CARDINALITY = 20


def _infer_column_type(s: pd.Series) -> ColumnType:
    if pd.api.types.is_bool_dtype(s):
        return ColumnType.BOOLEAN
    if pd.api.types.is_integer_dtype(s):
        return ColumnType.INTEGER
    if pd.api.types.is_float_dtype(s):
        # Same leniency as SchemaValidator: a float column of whole numbers is an
        # int column pandas promoted because it has a null. An all-null column
        # stays FLOAT, since there's nothing to suggest it's an integer.
        return ColumnType.INTEGER if not s.dropna().empty and is_integer_like(s) else ColumnType.FLOAT
    if pd.api.types.is_datetime64_any_dtype(s):
        return ColumnType.TIMESTAMP
    # Parquet decimal128/date32 arrive as object columns of Decimal/date values.
    # Require at least one value so an all-null object column stays a string.
    if not s.dropna().empty:
        if object_values_are(s, Decimal):
            return ColumnType.DECIMAL
        if object_values_are(s, date):
            return ColumnType.DATE
    return ColumnType.STRING


def _infer_column_spec(
    name: str,
    s: pd.Series,
    *,
    max_allowed_values_cardinality: int,
) -> dict[str, Any]:
    col_type = _infer_column_type(s)
    spec: dict[str, Any] = {
        "name": name,
        "type": col_type.value,
        "nullable": bool(s.isnull().any()),
    }
    if col_type == ColumnType.STRING:
        nunique = s.nunique(dropna=True)
        # Require values to actually repeat — an all-unique column (an
        # identifier) shouldn't be mistaken for a small fixed category set
        # just because the sample happens to be small.
        if 0 < nunique < len(s) and nunique <= max_allowed_values_cardinality:
            spec["allowed_values"] = sorted(s.dropna().unique().tolist())
    return spec


def _infer_quality_rule(name: str, s: pd.Series) -> dict[str, Any] | None:
    """Mirror QualityValidator's own null/duplicate-percentage formulas so
    that validating the same data against the inferred rule always passes.
    """
    total = len(s)
    if total == 0:
        return None

    rule: dict[str, Any] = {"column": name}
    has_rule = False

    null_pct = (s.isnull().sum() / total) * 100
    if null_pct == 0.0:
        rule["max_null_percentage"] = 0.0
        has_rule = True

    dup_pct = (s.duplicated().sum() / total) * 100
    if dup_pct == 0.0:
        rule["max_duplicate_percentage"] = 0.0
        has_rule = True

    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s) and not s.dropna().empty:
        rule["min_value"] = float(s.min())
        rule["max_value"] = float(s.max())
        has_rule = True

    return rule if has_rule else None


def profile_dataframe(
    df: pd.DataFrame,
    *,
    max_allowed_values_cardinality: int = DEFAULT_MAX_ALLOWED_VALUES_CARDINALITY,
) -> dict[str, Any]:
    """Profile *df* and return schema/volume/quality fragments for a
    starter DataContract. Pure function — no I/O.
    """
    row_count = len(df)
    columns = [
        _infer_column_spec(col, df[col], max_allowed_values_cardinality=max_allowed_values_cardinality)
        for col in df.columns
    ]
    quality = [
        rule for col in df.columns
        if (rule := _infer_quality_rule(col, df[col])) is not None
    ]

    profile: dict[str, Any] = {
        "schema": {"columns": columns},
        "quality": quality,
    }
    # A zero-row sample gives no usable signal — and a naive band around it
    # (e.g. max_rows: 0) would reject all future, legitimately non-empty data.
    if row_count > 0:
        profile["volume"] = {
            "min_rows": int(row_count * 0.5),
            "max_rows": int(row_count * 2),
        }
    return profile


def generate_contract(
    df: pd.DataFrame,
    *,
    name: str,
    dataset_format: str,
    owner_team: str,
    owner_email: str,
    location: str | None = None,
    table_name: str | None = None,
    connection_string: str | None = None,
    version: str = "0.1.0",
    max_allowed_values_cardinality: int = DEFAULT_MAX_ALLOWED_VALUES_CARDINALITY,
) -> DataContract:
    """Profile *df* and assemble a full, validated starter DataContract."""
    profile = profile_dataframe(df, max_allowed_values_cardinality=max_allowed_values_cardinality)

    dataset: dict[str, Any] = {"format": dataset_format}
    if location is not None:
        dataset["location"] = location
    if table_name is not None:
        dataset["table_name"] = table_name
    if connection_string is not None:
        dataset["connection_string"] = connection_string

    raw: dict[str, Any] = {
        "apiVersion": "datacontract/v1",
        "kind": "DataContract",
        "metadata": {
            "name": name,
            "version": version,
            "owner": {"team": owner_team, "email": owner_email},
        },
        "dataset": dataset,
        "on_breach": "warn",
        **profile,
    }
    return DataContract.model_validate(raw)


def contract_to_yaml_dict(contract: DataContract) -> dict[str, Any]:
    """Render *contract* as a plain dict for clean YAML dumping: every field that
    differs from its default, under its YAML name, with enums as plain strings.
    `on_breach` is always kept so a reader sees the mode even when it's the default.
    """
    d = contract.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True, mode="json")
    d["apiVersion"] = contract.api_version
    d.setdefault("on_breach", contract.on_breach)
    return d
