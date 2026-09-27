"""Akad — Data Contract Framework.

Public API::

    from akad import DataContractValidator, DataContractBreachError
"""
from akad.sdk import (
    DataContractBreachError,
    DataContractError,
    DataContractEvaluationError,
    DataContractValidator,
)

__all__ = [
    "DataContractBreachError",
    "DataContractError",
    "DataContractEvaluationError",
    "DataContractValidator",
]
__version__ = "1.4.0"
