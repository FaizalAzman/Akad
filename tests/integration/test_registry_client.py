"""Integration tests for RegistryClient against the real FastAPI registry app,
plus failure-tolerance tests against an unreachable host.
"""
from __future__ import annotations

import logging

import httpx
import pytest

from akad.models.result import ClauseResult, ClauseStatus, OverallStatus
from akad.registry_client import RegistryClient
from tests.conftest import make_contract, make_validation_result

# Nothing listens here — connection is refused immediately
DEAD_URL = "http://127.0.0.1:9"


class TestPostValidationResult:
    def test_posted_result_is_queryable(self, akad_registry_client, registry_client):
        result = make_validation_result(
            contract_name="post_test",
            status=OverallStatus.BREACH,
            clauses=[ClauseResult(
                clause_type="volume.min_rows",
                clause_target=None,
                status=ClauseStatus.FAIL,
                expected=">= 100 rows",
                observed="10 rows",
                message="Row count 10 below minimum 100",
            )],
        )

        akad_registry_client.post_validation_result(result)

        data = registry_client.get(
            "/validation-results/?contract_name=post_test"
        ).json()
        assert len(data) == 1
        assert data[0]["contract_name"] == "post_test"
        assert data[0]["overall_status"] == "BREACH"
        assert data[0]["row_count"] == 10

    def test_compliant_and_breach_both_recorded(self, akad_registry_client, registry_client):
        akad_registry_client.post_validation_result(
            make_validation_result(contract_name="mixed", status=OverallStatus.COMPLIANT)
        )
        akad_registry_client.post_validation_result(
            make_validation_result(contract_name="mixed", status=OverallStatus.BREACH)
        )

        data = registry_client.get("/validation-results/?contract_name=mixed").json()
        statuses = {r["overall_status"] for r in data}
        assert statuses == {"COMPLIANT", "BREACH"}


class TestListing:
    def test_contract_names_with_special_characters_are_escaped(self, akad_registry_client):
        """A name with '&' or spaces used to be pasted into the query string raw."""
        for name in ("sales & returns", "sales"):
            akad_registry_client.post_validation_result(make_validation_result(contract_name=name))

        runs = akad_registry_client.list_validation_results("sales & returns")

        assert [r["contract_name"] for r in runs] == ["sales & returns"]

    def test_limit_is_respected(self, akad_registry_client):
        for _ in range(3):
            akad_registry_client.post_validation_result(make_validation_result(contract_name="many"))
        assert len(akad_registry_client.list_validation_results("many", limit=2)) == 2

    def test_list_contracts_returns_current_versions(self, akad_registry_client):
        akad_registry_client.publish_contract(make_contract(name="listed", version="1.0.0"))
        akad_registry_client.publish_contract(make_contract(name="listed", version="1.1.0"))
        assert [(c["name"], c["version"]) for c in akad_registry_client.list_contracts()] == [("listed", "1.1.0")]


class TestFailureTolerance:
    """Registry being down must never break a pipeline run."""

    def test_post_result_swallows_connection_error(self, caplog):
        client = RegistryClient(DEAD_URL)
        with caplog.at_level(logging.WARNING):
            client.post_validation_result(make_validation_result())  # must not raise
        assert "Failed to post result" in caplog.text

    def test_publish_contract_propagates_connection_error(self):
        # A publish that silently didn't happen is worse than a loud failure
        with pytest.raises(httpx.HTTPError):
            RegistryClient(DEAD_URL).publish_contract(make_contract())

    def test_publish_contract_propagates_registry_rejection(self):
        transport = httpx.MockTransport(lambda _request: httpx.Response(500, text="boom"))
        client = RegistryClient("http://registry", _http_client=httpx.Client(transport=transport))
        with pytest.raises(httpx.HTTPStatusError):
            client.publish_contract(make_contract())

    def test_get_contract_propagates_connection_error(self):
        # Fetching the contract is load-bearing — this one MUST raise
        with pytest.raises(httpx.HTTPError):
            RegistryClient(DEAD_URL).get_contract("daily_sales")

    def test_get_contract_version_propagates_connection_error(self):
        with pytest.raises(httpx.HTTPError):
            RegistryClient(DEAD_URL).get_contract_version("daily_sales", "1.0.0")


class TestBaseUrlNormalisation:
    def test_trailing_slash_is_stripped(self):
        client = RegistryClient("http://localhost:8000/")
        assert client.base_url == "http://localhost:8000"
