from __future__ import annotations

from pathlib import Path

from akad import engine as eng
from akad.contract_loader import load_contract
from akad.models.result import OverallStatus, ValidationResult
from akad.notifier import dispatch_notifications
from akad.registry_client import RegistryClient


class DataContractValidator:
    """Main entry point for pipeline integration.

    Two ways to load the contract:

    1. From a local file (dev / CI)::

        DataContractValidator(contract_path="contracts/daily_sales.yaml").validate()

    2. From the registry by name — no local file needed (Airflow workers)::

        DataContractValidator(
            contract_name="daily_sales",
            registry_url="http://akad-registry:8000",
        ).validate()
    """

    def __init__(
        self,
        contract_path: str | Path | None = None,
        contract_name: str | None = None,
        registry_url: str | None = None,
        registry_token: str | None = None,
        extra_validators: list | None = None,
        notifiers: list | None = None,
        _registry_client: RegistryClient | None = None,  # injectable — used in tests
    ):
        if contract_path is not None and contract_name is not None:
            raise ValueError("Provide either contract_path or contract_name, not both.")
        if contract_path is None and contract_name is None:
            raise ValueError("One of contract_path or contract_name is required.")

        # Resolve which registry client to use
        self.registry: RegistryClient | None
        if _registry_client is not None:
            self.registry = _registry_client
        elif registry_url:
            # registry_token falls back to $AKAD_API_TOKEN inside RegistryClient
            self.registry = RegistryClient(registry_url, api_token=registry_token)
        else:
            self.registry = None

        if contract_name is not None:
            if not self.registry:
                raise ValueError("registry_url is required when using contract_name.")
            self.contract = self.registry.get_contract(contract_name)
        else:
            if contract_path is None:
                raise AssertionError("unreachable — guaranteed by the checks above")
            self.contract = load_contract(contract_path)

        self.extra_validators = extra_validators or []
        self._notifiers       = notifiers  # None → use defaults; [] → disable

    def validate(self) -> ValidationResult:
        """Run validation.

        - ``on_breach='warn'``: logs, notifies, returns result.
        - ``on_breach='fail'``: logs, notifies, raises :exc:`DataContractBreachError`
          on a breach, or :exc:`DataContractEvaluationError` if the contract
          couldn't be evaluated at all (unreadable dataset, erroring rule) —
          a gate that can't see the data must not let the pipeline through.
        """
        result = eng.validate(self.contract, self.extra_validators)

        if result.is_breach or result.overall_status == OverallStatus.ERROR:
            dispatch_notifications(self.contract, result, self._notifiers)

        if self.registry:
            self.registry.post_validation_result(result)

        if self.contract.on_breach == "fail":
            name = self.contract.metadata.name
            if result.is_breach:
                raise DataContractBreachError(
                    f'Contract "{name}" breached. {len(result.failed_clauses)} clause(s) failed.',
                    result=result,
                )
            if result.overall_status == OverallStatus.ERROR:
                reason = result.error_message or f"{len(result.errored_clauses)} clause(s) errored"
                raise DataContractEvaluationError(
                    f'Contract "{name}" could not be evaluated: {reason}',
                    result=result,
                )

        return result


class DataContractError(Exception):
    """Base for errors raised by :meth:`DataContractValidator.validate` under
    ``on_breach='fail'``. Carries the full :class:`ValidationResult`."""

    def __init__(self, message: str, result: ValidationResult):
        super().__init__(message)
        self.result = result


class DataContractBreachError(DataContractError):
    """The dataset was evaluated and violated the contract."""


class DataContractEvaluationError(DataContractError):
    """The contract could not be evaluated — e.g. the dataset was unreadable."""
