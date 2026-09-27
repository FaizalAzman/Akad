from __future__ import annotations

import warnings
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class UnknownContractKeyWarning(UserWarning):
    """A contract has a key Akad doesn't recognise, usually a typo such as
    `freshnes:` or `max_null_percentag:`. The key is ignored, so whatever
    rule it was meant to declare is not enforced. Unknown keys will be an
    error in the next major version; `akad check --strict` fails on them now.
    """


class _ContractModel(BaseModel):
    """Base for every contract section: warns about keys it doesn't define."""

    @model_validator(mode="before")
    @classmethod
    def _warn_on_unknown_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            known = set(cls.model_fields) | {f.alias for f in cls.model_fields.values() if f.alias}
            # A key set to null declares nothing, so there's no rule to lose. Contracts
            # published by earlier versions store removed fields that way.
            unknown = sorted(str(k) for k, v in data.items() if k not in known and v is not None)
            if unknown:
                warnings.warn(
                    f"{cls.__name__}: unknown key(s) {unknown} are ignored, so any rule they "
                    f"declare is not enforced. Expected one of: "
                    f"{sorted(f.alias or name for name, f in cls.model_fields.items())}",
                    UnknownContractKeyWarning,
                    stacklevel=2,
                )
        return data


class ColumnType(StrEnum):
    STRING    = "string"
    INTEGER   = "integer"
    FLOAT     = "float"
    BOOLEAN   = "boolean"
    DATE      = "date"
    TIMESTAMP = "timestamp"
    DECIMAL   = "decimal"


class ColumnSpec(_ContractModel):
    name:           str
    type:           ColumnType
    nullable:       bool = True
    description:    str | None = None
    allowed_values: list[str] | None = None


class SchemaSpec(_ContractModel):
    enforce_no_extra_columns: bool = False
    columns: list[ColumnSpec]


class FreshnessSpec(_ContractModel):
    max_age_hours: float
    check_column:  str | None = None


class VolumeSpec(_ContractModel):
    min_rows: int | None = None
    max_rows: int | None = None


class QualityRule(_ContractModel):
    column:                   str
    max_null_percentage:      float | None = None
    max_duplicate_percentage: float | None = None
    min_value:                float | None = None
    max_value:                float | None = None


class BusinessRule(_ContractModel):
    name:        str
    expression:  str
    description: str | None = None


class ConsumerSpec(_ContractModel):
    team:          str
    email:         str


class OwnerSpec(_ContractModel):
    team:  str
    email: str


class MetadataSpec(_ContractModel):
    name:        str
    version:     str
    description: str | None = None
    owner:       OwnerSpec
    tags:        list[str] = []


class DatasetSpec(_ContractModel):
    format:            Literal["parquet", "sql"]
    location:          str | None = None
    table_name:        str | None = None
    connection_string: str | None = None
    partition_column:  str | None = None


class WebhookSpec(_ContractModel):
    url:     str
    headers: dict[str, str] = {}


class EmailSpec(_ContractModel):
    smtp_host:         str
    smtp_port:         int = 587
    smtp_user:         str
    smtp_password_env: str
    recipients:        list[str] = []


class NotificationsSpec(_ContractModel):
    webhook: WebhookSpec | None = None
    email:   EmailSpec | None   = None


class DataContract(_ContractModel):
    api_version:    Literal["datacontract/v1"]    = Field(alias="apiVersion")
    kind:           Literal["DataContract"]
    metadata:       MetadataSpec
    dataset:        DatasetSpec
    on_breach:      Literal["warn", "fail"]       = "warn"
    consumers:      list[ConsumerSpec]            = []
    schema_:        SchemaSpec | None             = Field(None, alias="schema")
    freshness:      FreshnessSpec | None          = None
    volume:         VolumeSpec | None             = None
    quality:        list[QualityRule]             = []
    business_rules: list[BusinessRule]            = []
    notifications:  NotificationsSpec | None      = None

    model_config = {"populate_by_name": True}
