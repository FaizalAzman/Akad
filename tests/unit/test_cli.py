"""Tests for the akad CLI — check, validate, publish, list, history.

Uses Typer's CliRunner. Registry-backed commands patch httpx / RegistryClient
so no server is needed.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import yaml
from typer.testing import CliRunner

from akad.cli import app
from akad.models.contract import DataContract

runner = CliRunner()


def _write_contract_yaml(tmp_path, location, *, min_rows=1, on_breach="warn"):
    path = tmp_path / "contract.yaml"
    path.write_text(f"""\
apiVersion: datacontract/v1
kind: DataContract
metadata:
  name: cli_sales
  version: "1.0.0"
  owner:
    team: Test Team
    email: test@example.com
dataset:
  format: parquet
  location: {location}
on_breach: {on_breach}
volume:
  min_rows: {min_rows}
""")
    return path


class TestCheck:
    def test_valid_contract_exits_zero(self, tmp_path):
        path = _write_contract_yaml(tmp_path, "/tmp/whatever.parquet")
        result = runner.invoke(app, ["check", "--contract", str(path)])
        assert result.exit_code == 0
        assert "OK" in result.output
        assert "cli_sales v1.0.0" in result.output

    def test_invalid_yaml_exits_one(self, tmp_path):
        path = tmp_path / "broken.yaml"
        path.write_text("apiVersion: wrong/version\nkind: Nope\n")
        result = runner.invoke(app, ["check", "--contract", str(path)])
        assert result.exit_code == 1

    def test_missing_file_exits_one(self, tmp_path):
        result = runner.invoke(
            app, ["check", "--contract", str(tmp_path / "missing.yaml")]
        )
        assert result.exit_code == 1


class TestValidate:
    def test_compliant_exits_zero(self, tmp_path, tmp_parquet):
        path = _write_contract_yaml(tmp_path, tmp_parquet.as_posix())
        result = runner.invoke(app, ["validate", "--contract", str(path)])
        assert result.exit_code == 0
        assert "COMPLIANT" in result.output

    def test_breach_warn_mode_exits_one(self, tmp_path, tmp_parquet):
        path = _write_contract_yaml(tmp_path, tmp_parquet.as_posix(), min_rows=999)
        result = runner.invoke(app, ["validate", "--contract", str(path)])
        assert result.exit_code == 1
        assert "BREACH" in result.output
        assert "Failed clauses" in result.output

    def test_breach_fail_mode_exits_one(self, tmp_path, tmp_parquet):
        """on_breach: fail raises internally — CLI still prints result, exit 1."""
        path = _write_contract_yaml(tmp_path, tmp_parquet.as_posix(),
                                    min_rows=999, on_breach="fail")
        result = runner.invoke(app, ["validate", "--contract", str(path)])
        assert result.exit_code == 1
        assert "BREACH" in result.output

    def test_json_output(self, tmp_path, tmp_parquet):
        path = _write_contract_yaml(tmp_path, tmp_parquet.as_posix(), min_rows=999)
        result = runner.invoke(
            app, ["validate", "--contract", str(path), "--output", "json"]
        )
        payload = json.loads(result.output)
        assert payload["status"] == "BREACH"
        assert payload["row_count"] == 10
        assert payload["failed_clauses"][0]["clause_type"] == "volume.min_rows"

    def test_unreadable_dataset_warn_mode_exits_two(self, tmp_path):
        path = _write_contract_yaml(tmp_path, (tmp_path / "missing.parquet").as_posix())
        result = runner.invoke(app, ["validate", "--contract", str(path)])
        assert result.exit_code == 2
        assert "ERROR" in result.output
        assert "Failed to read dataset" in result.output

    def test_unreadable_dataset_fail_mode_exits_two(self, tmp_path):
        path = _write_contract_yaml(tmp_path, (tmp_path / "missing.parquet").as_posix(),
                                    on_breach="fail")
        result = runner.invoke(app, ["validate", "--contract", str(path)])
        assert result.exit_code == 2
        assert "Failed to read dataset" in result.output

    def test_error_json_output_includes_error_message(self, tmp_path):
        path = _write_contract_yaml(tmp_path, (tmp_path / "missing.parquet").as_posix())
        result = runner.invoke(
            app, ["validate", "--contract", str(path), "--output", "json"]
        )
        payload = json.loads(result.output)
        assert result.exit_code == 2
        assert payload["status"] == "ERROR"
        assert "Failed to read dataset" in payload["error_message"]
        assert payload["errored_clauses"] == []

    def test_unloadable_contract_exits_two(self, tmp_path):
        result = runner.invoke(
            app, ["validate", "--contract", str(tmp_path / "missing.yaml")]
        )
        assert result.exit_code == 2


class TestPublish:
    def test_publishes_and_echoes_name_version(self, tmp_path):
        path = _write_contract_yaml(tmp_path, "/tmp/x.parquet")
        with patch("akad.cli.RegistryClient") as client_cls:
            result = runner.invoke(app, [
                "publish", "--contract", str(path),
                "--registry-url", "http://localhost:8000",
            ])

        assert result.exit_code == 0
        assert "Published cli_sales v1.0.0" in result.output
        client_cls.assert_called_once_with("http://localhost:8000", api_token=None)
        client_cls.return_value.publish_contract.assert_called_once()

    def test_identical_republish_says_nothing_changed(self, tmp_path):
        path = _write_contract_yaml(tmp_path, "/tmp/x.parquet")
        with patch("akad.cli.RegistryClient") as client_cls:
            client_cls.return_value.publish_contract.return_value = False
            result = runner.invoke(app, [
                "publish", "--contract", str(path), "--registry-url", "http://localhost:8000",
            ])
        assert result.exit_code == 0
        assert "Already published cli_sales v1.0.0" in result.output

    def test_registry_rejection_exits_one_with_detail(self, tmp_path):
        path = _write_contract_yaml(tmp_path, "/tmp/x.parquet")
        request = httpx.Request("POST", "http://localhost:8000/contracts/")
        rejection = httpx.HTTPStatusError(
            "conflict", request=request,
            response=httpx.Response(409, text='{"detail":"version exists"}', request=request),
        )
        with patch("akad.cli.RegistryClient") as client_cls:
            client_cls.return_value.publish_contract.side_effect = rejection
            result = runner.invoke(app, [
                "publish", "--contract", str(path),
                "--registry-url", "http://localhost:8000",
            ])

        assert result.exit_code == 1
        assert "409" in result.output
        assert "version exists" in result.output
        assert "Published" not in result.output

    def test_unreachable_registry_exits_one(self, tmp_path):
        path = _write_contract_yaml(tmp_path, "/tmp/x.parquet")
        result = runner.invoke(app, [
            "publish", "--contract", str(path), "--registry-url", "http://127.0.0.1:9",
        ])
        assert result.exit_code == 1
        assert "could not publish cli_sales v1.0.0" in result.output
        assert "Published" not in result.output


class TestList:
    def test_lists_contracts(self):
        resp = MagicMock()
        resp.json.return_value = [
            {"name": "daily_sales", "version": "1.0.0"},
            {"name": "weekly_revenue", "version": "2.1.0"},
        ]
        with patch("httpx.get", return_value=resp) as get:
            result = runner.invoke(
                app, ["list", "--registry-url", "http://localhost:8000/"]
            )

        assert result.exit_code == 0
        assert "daily_sales" in result.output
        assert "v2.1.0" in result.output
        # trailing slash on registry-url must not produce a double slash
        assert get.call_args[0][0] == "http://localhost:8000/contracts/"

    def test_unreachable_registry_exits_one(self):
        with patch("httpx.get", side_effect=ConnectionError("refused")):
            result = runner.invoke(
                app, ["list", "--registry-url", "http://localhost:8000"]
            )
        assert result.exit_code == 1


class TestInfer:
    def test_infers_contract_to_stdout(self, tmp_parquet):
        result = runner.invoke(app, [
            "infer", "--name", "transactions", "--location", str(tmp_parquet),
        ])
        assert result.exit_code == 0
        # strip the leading `#` comment header before parsing as YAML
        body = "\n".join(
            line for line in result.output.splitlines() if not line.startswith("#")
        )
        contract = DataContract.model_validate(yaml.safe_load(body))
        assert contract.metadata.name == "transactions"
        assert contract.dataset.location == str(tmp_parquet)
        assert {c.name for c in contract.schema_.columns} == {
            "transaction_id", "amount", "currency_code", "status",
        }

    def test_infers_contract_to_output_file(self, tmp_path, tmp_parquet):
        out = tmp_path / "starter.yaml"
        result = runner.invoke(app, [
            "infer", "--name", "transactions", "--location", str(tmp_parquet),
            "--output", str(out),
        ])
        assert result.exit_code == 0
        assert "Wrote starter contract" in result.output
        assert out.exists()
        body = "\n".join(
            line for line in out.read_text().splitlines() if not line.startswith("#")
        )
        contract = DataContract.model_validate(yaml.safe_load(body))
        assert contract.metadata.name == "transactions"

    def test_requires_location_for_parquet(self):
        result = runner.invoke(app, ["infer", "--name", "x"])
        assert result.exit_code == 2
        assert "--location is required" in result.output

    def test_requires_connection_string_and_table_for_sql(self):
        result = runner.invoke(app, ["infer", "--name", "x", "--format", "sql"])
        assert result.exit_code == 2
        assert "--connection-string and --table-name are required" in result.output

    def test_rejects_unsupported_format(self):
        result = runner.invoke(app, ["infer", "--name", "x", "--format", "csv"])
        assert result.exit_code == 2
        assert "unsupported format" in result.output


def _write_volume_contract(tmp_path, filename, *, min_rows):
    path = tmp_path / filename
    path.write_text(f"""\
apiVersion: datacontract/v1
kind: DataContract
metadata:
  name: cli_sales
  version: "1.0.0"
  owner:
    team: Test Team
    email: test@example.com
dataset:
  format: parquet
  location: /tmp/x.parquet
on_breach: warn
volume:
  min_rows: {min_rows}
""")
    return path


class TestDiff:
    def test_no_breaking_changes_exits_zero(self, tmp_path):
        old = _write_volume_contract(tmp_path, "old.yaml", min_rows=500)
        new = _write_volume_contract(tmp_path, "new.yaml", min_rows=1000)  # tightened
        result = runner.invoke(app, ["diff", "--old", str(old), "--new", str(new)])
        assert result.exit_code == 0
        assert "NON_BREAKING" in result.output

    def test_breaking_change_exits_one(self, tmp_path):
        old = _write_volume_contract(tmp_path, "old.yaml", min_rows=1000)
        new = _write_volume_contract(tmp_path, "new.yaml", min_rows=500)  # loosened
        result = runner.invoke(app, ["diff", "--old", str(old), "--new", str(new)])
        assert result.exit_code == 1
        assert "BREAKING" in result.output
        assert "breaking" in result.output  # summary line

    def test_no_differences_message(self, tmp_path):
        old = _write_volume_contract(tmp_path, "old.yaml", min_rows=500)
        new = _write_volume_contract(tmp_path, "new.yaml", min_rows=500)
        result = runner.invoke(app, ["diff", "--old", str(old), "--new", str(new)])
        assert result.exit_code == 0
        assert "No differences detected" in result.output

    def test_json_output(self, tmp_path):
        old = _write_volume_contract(tmp_path, "old.yaml", min_rows=1000)
        new = _write_volume_contract(tmp_path, "new.yaml", min_rows=500)
        result = runner.invoke(app, [
            "diff", "--old", str(old), "--new", str(new), "--output", "json",
        ])
        payload = json.loads(result.output)
        assert payload[0]["severity"] == "BREAKING"
        assert payload[0]["path"] == "volume.min_rows"

    def test_requires_old_and_new_or_registry_args(self):
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 2
        assert "provide --old/--new" in result.output

    def test_name_mode_requires_all_registry_args(self):
        result = runner.invoke(app, ["diff", "--name", "x", "--old-version", "1.0.0"])
        assert result.exit_code == 2
        assert "requires --old-version, --new-version, and --registry-url" in result.output

    def test_compares_two_registry_versions(self, tmp_path):
        old = _write_volume_contract(tmp_path, "old.yaml", min_rows=1000)
        new = _write_volume_contract(tmp_path, "new.yaml", min_rows=500)
        from akad.contract_loader import load_contract

        with patch("akad.cli.RegistryClient") as client_cls:
            instance = client_cls.return_value
            instance.get_contract_version.side_effect = [
                load_contract(old), load_contract(new),
            ]
            result = runner.invoke(app, [
                "diff", "--name", "cli_sales",
                "--old-version", "1.0.0", "--new-version", "2.0.0",
                "--registry-url", "http://localhost:8000",
            ])

        assert result.exit_code == 1
        assert "BREAKING" in result.output
        client_cls.assert_called_once_with("http://localhost:8000", api_token=None)
        instance.get_contract_version.assert_any_call("cli_sales", "1.0.0")
        instance.get_contract_version.assert_any_call("cli_sales", "2.0.0")

    def test_unloadable_old_contract_exits_two(self, tmp_path):
        new = _write_volume_contract(tmp_path, "new.yaml", min_rows=500)
        result = runner.invoke(app, [
            "diff", "--old", str(tmp_path / "missing.yaml"), "--new", str(new),
        ])
        assert result.exit_code == 2


class TestHistory:
    def test_shows_validation_runs(self):
        resp = MagicMock()
        resp.json.return_value = [
            {"validated_at": "2026-06-13T00:00:00", "overall_status": "COMPLIANT"},
            {"validated_at": "2026-06-12T00:00:00", "overall_status": "BREACH"},
        ]
        with patch("httpx.get", return_value=resp) as get:
            result = runner.invoke(app, [
                "history", "--name", "daily_sales",
                "--registry-url", "http://localhost:8000/",
                "--limit", "5",
            ])

        assert result.exit_code == 0
        assert "COMPLIANT" in result.output
        assert "BREACH" in result.output
        url = get.call_args[0][0]
        assert url.startswith("http://localhost:8000/validation-results/")
        assert "contract_name=daily_sales" in url
        assert "limit=5" in url

    def test_unreachable_registry_exits_one(self):
        with patch("httpx.get", side_effect=ConnectionError("refused")):
            result = runner.invoke(app, [
                "history", "--name", "x", "--registry-url", "http://localhost:8000",
            ])
        assert result.exit_code == 1
