from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ClauseStatus(StrEnum):
    PASS    = "PASS"  # noqa: S105 — enum member, not a credential
    FAIL    = "FAIL"
    SKIPPED = "SKIPPED"
    ERROR   = "ERROR"


class OverallStatus(StrEnum):
    COMPLIANT = "COMPLIANT"
    BREACH    = "BREACH"
    ERROR     = "ERROR"


@dataclass
class ClauseResult:
    clause_type:   str
    clause_target: str | None
    status:        ClauseStatus
    expected:      Any
    observed:      Any
    message:       str

    def to_dict(self) -> dict:
        """JSON-safe dict matching the registry's ClauseResultSchema."""
        return {
            "clause_type":   self.clause_type,
            "clause_target": self.clause_target,
            "status":        self.status.value,
            "expected":      str(self.expected),
            "observed":      str(self.observed),
            "message":       self.message,
        }

    @classmethod
    def check(
        cls,
        clause_type: str,
        clause_target: str | None,
        ok: bool,
        *,
        expected: Any,
        observed: Any,
        fail_message: str = "",
    ) -> ClauseResult:
        """Build a PASS/FAIL clause — the shape every validator repeats."""
        return cls(
            clause_type=clause_type,
            clause_target=clause_target,
            status=ClauseStatus.PASS if ok else ClauseStatus.FAIL,
            expected=expected,
            observed=observed,
            message="" if ok else fail_message,
        )


@dataclass
class ValidationResult:
    contract_name:    str
    contract_version: str
    dataset_location: str
    validated_at:     datetime
    overall_status:   OverallStatus
    clause_results:   list[ClauseResult] = field(default_factory=list)
    row_count:        int | None = None
    error_message:    str | None = None

    @property
    def is_breach(self) -> bool:
        return self.overall_status == OverallStatus.BREACH

    @property
    def failed_clauses(self) -> list[ClauseResult]:
        return [c for c in self.clause_results if c.status == ClauseStatus.FAIL]

    @property
    def errored_clauses(self) -> list[ClauseResult]:
        return [c for c in self.clause_results if c.status == ClauseStatus.ERROR]

    def to_dict(self) -> dict:
        """JSON-safe dict matching the registry's ValidationResultRequest payload."""
        return {
            "contract_name":    self.contract_name,
            "contract_version": self.contract_version,
            "dataset_location": self.dataset_location,
            "validated_at":     self.validated_at.isoformat(),
            "overall_status":   self.overall_status.value,
            "row_count":        self.row_count,
            "clause_results":   [c.to_dict() for c in self.clause_results],
            "error_message":    self.error_message,
        }
