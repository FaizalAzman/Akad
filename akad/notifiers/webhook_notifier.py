from __future__ import annotations

import logging

import httpx

from akad.models.contract import DataContract
from akad.models.result import ValidationResult
from akad.notifiers.base import Notifier

log = logging.getLogger(__name__)


def _build_payload(contract: DataContract, result: ValidationResult) -> dict:
    return {
        # DATA_CONTRACT_BREACH when the data violated the contract,
        # DATA_CONTRACT_ERROR when the contract couldn't be evaluated at all.
        "event":            f"DATA_CONTRACT_{result.overall_status.value}",
        "overall_status":   result.overall_status.value,
        "contract_name":    result.contract_name,
        "contract_version": result.contract_version,
        "dataset_location": result.dataset_location,
        "validated_at":     result.validated_at.isoformat(),
        "row_count":        result.row_count,
        "error_message":    result.error_message,
        "failed_clauses":   [c.to_dict() for c in result.failed_clauses],
        "errored_clauses":  [c.to_dict() for c in result.errored_clauses],
        "on_breach": contract.on_breach,
    }


class WebhookNotifier(Notifier):
    def notify(self, contract: DataContract, result: ValidationResult) -> None:
        if not (contract.notifications and contract.notifications.webhook):
            return
        cfg = contract.notifications.webhook
        payload = _build_payload(contract, result)
        try:
            resp = httpx.post(
                cfg.url,
                json=payload,
                headers=cfg.headers,
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            log.warning("Webhook notification failed: %s", exc)
