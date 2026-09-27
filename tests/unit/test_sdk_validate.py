"""Tests for DataContractValidator.validate() — warn vs fail semantics,
notification dispatch, and registry result posting.

Uses a fake in-memory registry so no server is needed.
"""
from __future__ import annotations

import pytest

from akad import (
    DataContractBreachError,
    DataContractError,
    DataContractEvaluationError,
    DataContractValidator,
)
from akad.models.result import OverallStatus
from tests.conftest import make_contract


class FakeRegistry:
    """In-memory stand-in for RegistryClient."""

    def __init__(self, contract=None):
        self.contract = contract
        self.posted = []

    def get_contract(self, _name):
        return self.contract

    def post_validation_result(self, result):
        self.posted.append(result)


def _validator(contract, notifiers=None):
    return DataContractValidator(
        contract_name=contract.metadata.name,
        _registry_client=FakeRegistry(contract),
        notifiers=notifiers,
    )


class TestWarnMode:
    def test_breach_returns_result_without_raising(self, tmp_parquet):
        contract = make_contract(location=str(tmp_parquet), on_breach="warn",
                                 volume={"min_rows": 999})  # 10 rows → breach
        result = _validator(contract, notifiers=[]).validate()

        assert result.overall_status == OverallStatus.BREACH
        assert result.is_breach
        assert len(result.failed_clauses) == 1

    def test_breach_dispatches_notifications(self, tmp_parquet, recording_notifier):
        contract = make_contract(location=str(tmp_parquet), on_breach="warn",
                                 volume={"min_rows": 999})
        _validator(contract, notifiers=[recording_notifier]).validate()

        assert len(recording_notifier.calls) == 1
        notified_contract, notified_result = recording_notifier.calls[0]
        assert notified_contract is contract
        assert notified_result.is_breach

    def test_breach_result_is_posted_to_registry(self, tmp_parquet):
        contract = make_contract(location=str(tmp_parquet), on_breach="warn",
                                 volume={"min_rows": 999})
        validator = _validator(contract, notifiers=[])
        validator.validate()

        assert len(validator.registry.posted) == 1
        assert validator.registry.posted[0].overall_status == OverallStatus.BREACH


class TestFailMode:
    def test_breach_raises_with_result_attached(self, tmp_parquet):
        contract = make_contract(location=str(tmp_parquet), on_breach="fail",
                                 volume={"min_rows": 999})
        with pytest.raises(DataContractBreachError) as exc_info:
            _validator(contract, notifiers=[]).validate()

        assert exc_info.value.result.is_breach
        assert "test_contract" in str(exc_info.value)

    def test_notifies_and_posts_before_raising(self, tmp_parquet, recording_notifier):
        contract = make_contract(location=str(tmp_parquet), on_breach="fail",
                                 volume={"min_rows": 999})
        validator = _validator(contract, notifiers=[recording_notifier])

        with pytest.raises(DataContractBreachError):
            validator.validate()

        assert len(recording_notifier.calls) == 1
        assert len(validator.registry.posted) == 1


class TestCompliantRun:
    def test_no_notifications_on_compliant_result(self, tmp_parquet, recording_notifier):
        contract = make_contract(location=str(tmp_parquet),
                                 volume={"min_rows": 1, "max_rows": 100})
        result = _validator(contract, notifiers=[recording_notifier]).validate()

        assert result.overall_status == OverallStatus.COMPLIANT
        assert recording_notifier.calls == []

    def test_compliant_result_still_posted_to_registry(self, tmp_parquet):
        contract = make_contract(location=str(tmp_parquet),
                                 volume={"min_rows": 1})
        validator = _validator(contract, notifiers=[])
        validator.validate()

        assert len(validator.registry.posted) == 1
        assert validator.registry.posted[0].overall_status == OverallStatus.COMPLIANT

    def test_fail_mode_does_not_raise_when_compliant(self, tmp_parquet):
        contract = make_contract(location=str(tmp_parquet), on_breach="fail",
                                 volume={"min_rows": 1})
        result = _validator(contract, notifiers=[]).validate()
        assert result.overall_status == OverallStatus.COMPLIANT


class TestErrorStatus:
    def test_error_result_dispatches_notifications(self, tmp_path, recording_notifier):
        """ERROR (unreadable data) also notifies — silence would hide outages."""
        contract = make_contract(location=str(tmp_path / "missing.parquet"))
        result = _validator(contract, notifiers=[recording_notifier]).validate()

        assert result.overall_status == OverallStatus.ERROR
        assert len(recording_notifier.calls) == 1

    def test_warn_mode_returns_error_result_without_raising(self, tmp_path):
        contract = make_contract(location=str(tmp_path / "missing.parquet"), on_breach="warn")
        result = _validator(contract, notifiers=[]).validate()
        assert result.overall_status == OverallStatus.ERROR

    def test_fail_mode_unreadable_dataset_raises(self, tmp_path):
        """A fail-mode gate that can't read the data must not let the pipeline through."""
        contract = make_contract(location=str(tmp_path / "missing.parquet"), on_breach="fail")
        with pytest.raises(DataContractEvaluationError) as exc_info:
            _validator(contract, notifiers=[]).validate()

        assert isinstance(exc_info.value, DataContractError)
        assert not isinstance(exc_info.value, DataContractBreachError)
        assert exc_info.value.result.overall_status == OverallStatus.ERROR
        assert "Failed to read dataset" in str(exc_info.value)

    def test_fail_mode_errored_clause_raises(self, tmp_parquet):
        contract = make_contract(location=str(tmp_parquet), on_breach="fail",
                                 business_rules=[{"name": "bad", "expression": "no_such_column > 0"}])
        with pytest.raises(DataContractEvaluationError, match=r"1 clause\(s\) errored"):
            _validator(contract, notifiers=[]).validate()

    def test_fail_mode_error_notifies_and_posts_before_raising(self, tmp_path, recording_notifier):
        contract = make_contract(location=str(tmp_path / "missing.parquet"), on_breach="fail")
        validator = _validator(contract, notifiers=[recording_notifier])

        with pytest.raises(DataContractEvaluationError):
            validator.validate()

        assert len(recording_notifier.calls) == 1
        assert len(validator.registry.posted) == 1


class TestRegistryResolution:
    def test_registry_url_builds_real_client(self, tmp_parquet, tmp_path):
        from akad.registry_client import RegistryClient

        yaml_path = tmp_path / "contract.yaml"
        yaml_path.write_text(f"""\
apiVersion: datacontract/v1
kind: DataContract
metadata:
  name: url_based
  version: "1.0.0"
  owner:
    team: Test Team
    email: test@example.com
dataset:
  format: parquet
  location: {tmp_parquet.as_posix()}
""")
        validator = DataContractValidator(
            contract_path=yaml_path,
            registry_url="http://localhost:8000",
            notifiers=[],
        )
        assert isinstance(validator.registry, RegistryClient)
        assert validator.registry.base_url == "http://localhost:8000"


class TestNoRegistry:
    def test_path_based_validation_without_registry(self, tmp_parquet, tmp_path):
        """contract_path with no registry_url → validates fine, posts nothing."""
        yaml_path = tmp_path / "contract.yaml"
        yaml_path.write_text(f"""\
apiVersion: datacontract/v1
kind: DataContract
metadata:
  name: local_only
  version: "1.0.0"
  owner:
    team: Test Team
    email: test@example.com
dataset:
  format: parquet
  location: {tmp_parquet.as_posix()}
on_breach: warn
volume:
  min_rows: 1
""")
        validator = DataContractValidator(contract_path=yaml_path, notifiers=[])
        result = validator.validate()

        assert validator.registry is None
        assert result.overall_status == OverallStatus.COMPLIANT
