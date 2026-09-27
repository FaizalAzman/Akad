"""Bearer-token auth on the registry: writes always need a token, reads only
when AKAD_REGISTRY_READS_REQUIRE_AUTH is set. Also covers the client side:
RegistryClient, the SDK and the CLI sending the token.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from akad.cli import app as cli_app
from akad.registry_client import RegistryClient
from registry.main import app
from tests.conftest import REGISTRY_TEST_TOKEN, make_contract, make_validation_result

CONTRACT = {
    "name": "secured", "version": "1.0.0",
    "content": {
        "apiVersion": "datacontract/v1", "kind": "DataContract",
        "metadata": {"name": "secured", "version": "1.0.0", "owner": {"team": "T", "email": "t@t.com"}},
        "dataset": {"format": "parquet", "location": "/tmp/x.parquet"},
    },
}
RESULT = make_validation_result(contract_name="secured").to_dict()


@pytest.fixture()
def anonymous(registry_client):  # noqa: ARG001 — needed for its database override and token config
    """A client for the same test registry that sends no Authorization header."""
    return TestClient(app)


class TestWritesRequireToken:
    @pytest.mark.parametrize(("path", "body"), [("/contracts/", CONTRACT), ("/validation-results/", RESULT)])
    def test_missing_token_is_rejected(self, anonymous, path, body):
        resp = anonymous.post(path, json=body)
        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.parametrize(("path", "body"), [("/contracts/", CONTRACT), ("/validation-results/", RESULT)])
    def test_wrong_token_is_rejected(self, anonymous, path, body):
        resp = anonymous.post(path, json=body, headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401

    def test_non_bearer_scheme_is_rejected(self, anonymous):
        resp = anonymous.post("/contracts/", json=CONTRACT, headers={"Authorization": f"Basic {REGISTRY_TEST_TOKEN}"})
        assert resp.status_code == 401

    def test_valid_token_is_accepted(self, registry_client):
        assert registry_client.post("/contracts/", json=CONTRACT).status_code == 201

    def test_any_configured_token_is_accepted(self, anonymous, monkeypatch):
        monkeypatch.setenv("AKAD_API_TOKENS", f"ci-token, {REGISTRY_TEST_TOKEN} ,airflow-token")
        resp = anonymous.post("/contracts/", json=CONTRACT, headers={"Authorization": "Bearer airflow-token"})
        assert resp.status_code == 201

    def test_no_configured_tokens_refuses_every_write(self, registry_client, monkeypatch):
        monkeypatch.delenv("AKAD_API_TOKENS")
        resp = registry_client.post("/contracts/", json=CONTRACT)
        assert resp.status_code == 401
        assert "AKAD_API_TOKENS" in resp.json()["detail"]


class TestReads:
    def test_reads_are_open_by_default(self, registry_client, anonymous):
        registry_client.post("/contracts/", json=CONTRACT)
        assert anonymous.get("/contracts/secured").status_code == 200
        assert anonymous.get("/validation-results/").status_code == 200

    def test_reads_can_require_a_token(self, registry_client, anonymous, monkeypatch):
        monkeypatch.setenv("AKAD_REGISTRY_READS_REQUIRE_AUTH", "true")
        assert anonymous.get("/contracts/").status_code == 401
        assert anonymous.get("/validation-results/").status_code == 401
        assert registry_client.get("/contracts/").status_code == 200

    def test_health_stays_open_when_reads_require_auth(self, anonymous, monkeypatch):
        monkeypatch.setenv("AKAD_REGISTRY_READS_REQUIRE_AUTH", "true")
        assert anonymous.get("/health/").status_code == 200


def _capturing_client() -> tuple[RegistryClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


class TestClientSendsToken:
    def test_explicit_token_is_sent(self, monkeypatch):
        monkeypatch.delenv("AKAD_API_TOKEN", raising=False)
        http, seen = _capturing_client()
        RegistryClient("http://r", _http_client=http, api_token="explicit").publish_contract(make_contract())
        assert seen[0].headers["Authorization"] == "Bearer explicit"

    def test_token_falls_back_to_environment(self, monkeypatch):
        monkeypatch.setenv("AKAD_API_TOKEN", "from-env")
        http, seen = _capturing_client()
        RegistryClient("http://r", _http_client=http).post_validation_result(make_validation_result())
        assert seen[0].headers["Authorization"] == "Bearer from-env"

    def test_no_token_sends_no_header(self, monkeypatch):
        monkeypatch.delenv("AKAD_API_TOKEN", raising=False)
        http, seen = _capturing_client()
        RegistryClient("http://r", _http_client=http).publish_contract(make_contract())
        assert "Authorization" not in seen[0].headers

    @pytest.mark.usefixtures("registry_client")
    def test_end_to_end_publish_with_token(self):
        client = RegistryClient("http://testserver", _http_client=TestClient(app), api_token=REGISTRY_TEST_TOKEN)
        assert client.publish_contract(make_contract(name="e2e")) is True
        assert client.publish_contract(make_contract(name="e2e")) is False  # identical: no-op
        assert client.get_contract("e2e").metadata.name == "e2e"

    @pytest.mark.usefixtures("registry_client")
    def test_end_to_end_publish_without_token_raises(self, monkeypatch):
        monkeypatch.delenv("AKAD_API_TOKEN", raising=False)
        client = RegistryClient("http://testserver", _http_client=TestClient(app))
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.publish_contract(make_contract(name="e2e"))
        assert exc_info.value.response.status_code == 401


class TestCliToken:
    runner = CliRunner()

    def test_publish_passes_token_option(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text(
            "apiVersion: datacontract/v1\nkind: DataContract\n"
            "metadata: {name: c, version: '1.0.0', owner: {team: t, email: e}}\n"
            "dataset: {format: parquet, location: /tmp/x.parquet}\n"
        )
        with patch("akad.cli.RegistryClient") as client_cls:
            result = self.runner.invoke(cli_app, [
                "publish", "--contract", str(path), "--registry-url", "http://r", "--token", "cli-token",
            ])
        assert result.exit_code == 0
        client_cls.assert_called_once_with("http://r", api_token="cli-token")

    def test_list_sends_token_from_environment(self, monkeypatch):
        monkeypatch.setenv("AKAD_API_TOKEN", "env-token")
        resp = MagicMock()
        resp.json.return_value = []
        with patch("httpx.get", return_value=resp) as get:
            result = self.runner.invoke(cli_app, ["list", "--registry-url", "http://r"])
        assert result.exit_code == 0
        assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer env-token"}
