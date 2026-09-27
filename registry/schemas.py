from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# MAJOR.MINOR.PATCH with optional pre-release and build metadata (semver.org).
SEMVER_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$"


class ContractPublishRequest(BaseModel):
    name:    str
    version: str = Field(pattern=SEMVER_PATTERN)
    content: dict[str, Any]


class ContractSummary(BaseModel):
    id:           int
    name:         str
    version:      str
    published_at: datetime
    is_current:   bool

    model_config = {"from_attributes": True}


def _parse_json_column(value: Any) -> Any:
    """The registry stores JSON documents in Text columns; parse them on the way out."""
    return json.loads(value) if isinstance(value, str) else value


class ContractDetail(ContractSummary):
    content: dict[str, Any]

    model_config = {"from_attributes": True}

    _parse_content = field_validator("content", mode="before")(_parse_json_column)


class ClauseResultSchema(BaseModel):
    clause_type:   str
    clause_target: str | None
    status:        str
    expected:      str
    observed:      str
    message:       str


class ValidationResultRequest(BaseModel):
    contract_name:    str
    contract_version: str
    dataset_location: str
    validated_at:     datetime
    overall_status:   str
    row_count:        int | None   = None
    clause_results:   list[ClauseResultSchema]
    error_message:    str | None   = None


class ValidationResultSummary(BaseModel):
    id:               int
    contract_name:    str
    contract_version: str
    dataset_location: str
    validated_at:     datetime
    overall_status:   str
    row_count:        int | None
    error_message:    str | None

    model_config = {"from_attributes": True}


class ValidationResultDetail(ValidationResultSummary):
    clause_results: list[ClauseResultSchema]

    model_config = {"from_attributes": True}

    _parse_clause_results = field_validator("clause_results", mode="before")(_parse_json_column)
