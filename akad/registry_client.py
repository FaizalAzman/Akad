from __future__ import annotations

import logging
from typing import Any

import httpx

from akad.models.contract import DataContract
from akad.models.result import ValidationResult

log = logging.getLogger(__name__)


class RegistryClient:
    def __init__(self, base_url: str, _http_client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        # Injected client is used in tests (ASGI transport); None → real httpx calls
        self._http = _http_client

    # ── internal helpers ──────────────────────────────────────────────────────

    def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}{path}"
        if self._http:
            return self._http.get(url, **kwargs)
        return httpx.get(url, **kwargs)

    def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}{path}"
        if self._http:
            return self._http.post(url, **kwargs)
        return httpx.post(url, **kwargs)

    # ── public API ────────────────────────────────────────────────────────────

    def get_contract(self, name: str) -> DataContract:
        """Fetch the current version of a contract from the registry by name.

        Raises httpx.HTTPStatusError if the contract is not found (404).
        """
        resp = self._get(f"/contracts/{name}", timeout=10)
        resp.raise_for_status()
        return DataContract.model_validate(resp.json()["content"])

    def get_contract_version(self, name: str, version: str) -> DataContract:
        """Fetch a specific historical version of a contract — used by `akad diff`.

        Raises httpx.HTTPStatusError if that version doesn't exist (404).
        """
        resp = self._get(f"/contracts/{name}/versions/{version}", timeout=10)
        resp.raise_for_status()
        return DataContract.model_validate(resp.json()["content"])

    def publish_contract(self, contract: DataContract) -> None:
        """Register a contract version.

        Raises httpx.HTTPError if the registry can't be reached or rejects the
        contract — unlike post_validation_result, a publish that silently
        didn't happen leaves consumers resolving a stale contract.
        """
        payload = {
            "name":    contract.metadata.name,
            "version": contract.metadata.version,
            "content": contract.model_dump(by_alias=True),
        }
        self._post("/contracts/", json=payload, timeout=10).raise_for_status()

    def post_validation_result(self, result: ValidationResult) -> None:
        try:
            self._post("/validation-results/", json=result.to_dict(), timeout=5).raise_for_status()
        except Exception as exc:
            log.warning("Failed to post result to registry: %s", exc)
