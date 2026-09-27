"""Unknown keys in a contract (usually typos) are ignored, so the rule they were
meant to declare silently disappears. Akad warns about them, and
`akad check --strict` fails on them."""
from __future__ import annotations

import warnings

import pytest
from typer.testing import CliRunner

from akad.cli import app
from akad.models.contract import DataContract, UnknownContractKeyWarning
from tests.conftest import make_contract

BASE = {
    "apiVersion": "datacontract/v1",
    "kind": "DataContract",
    "metadata": {"name": "x", "version": "1.0.0", "owner": {"team": "t", "email": "e"}},
    "dataset": {"format": "parquet", "location": "x"},
}

CONTRACT_WITH_TYPO = """\
apiVersion: datacontract/v1
kind: DataContract
metadata: {name: typo, version: "1.0.0", owner: {team: t, email: e}}
dataset: {format: parquet, location: /tmp/x.parquet}
freshnes: {max_age_hours: 24}
"""


class TestModelWarnings:
    def test_top_level_typo_warns_with_the_key_and_expected_names(self):
        with pytest.warns(UnknownContractKeyWarning, match=r"DataContract: unknown key\(s\) \['freshnes'\]") as record:
            DataContract.model_validate({**BASE, "freshnes": {"max_age_hours": 1}})
        # the expected keys are shown under their YAML names
        assert "'apiVersion'" in str(record[0].message)
        assert "'schema'" in str(record[0].message)

    def test_nested_typo_names_its_section(self):
        with pytest.warns(UnknownContractKeyWarning, match=r"QualityRule: unknown key\(s\) \['max_null_percentag'\]"):
            DataContract.model_validate({**BASE, "quality": [{"column": "a", "max_null_percentag": 0.0}]})

    def test_valid_contract_does_not_warn(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnknownContractKeyWarning)
            make_contract(
                schema_columns=[{"name": "a", "type": "string", "nullable": False}],
                freshness={"max_age_hours": 1}, volume={"min_rows": 1},
                quality=[{"column": "a", "max_null_percentage": 0.0}],
                business_rules=[{"name": "r", "expression": "a == a"}],
                notifications={"webhook": {"url": "https://example.com"}},
                consumers=[{"team": "c", "email": "c@example.com"}],
            )

    def test_null_unknown_keys_do_not_warn(self):
        """Contracts published by earlier versions carry removed fields as nulls;
        fetching them from the registry must not warn on every run."""
        stored = {**BASE, "dataset": {**BASE["dataset"], "catalog_uri": None, "namespace": None},
                  "consumers": [{"team": "c", "email": "c@example.com", "slack_webhook": None}]}
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnknownContractKeyWarning)
            DataContract.model_validate(stored)

    def test_removed_placeholder_fields_now_warn(self):
        with pytest.warns(UnknownContractKeyWarning, match="catalog_uri"):
            DataContract.model_validate({**BASE, "dataset": {**BASE["dataset"], "catalog_uri": "x"}})


class TestCli:
    runner = CliRunner()

    def _write(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text(CONTRACT_WITH_TYPO)
        return path

    def test_check_warns_but_passes(self, tmp_path):
        result = self.runner.invoke(app, ["check", "--contract", str(self._write(tmp_path))])
        assert result.exit_code == 0
        assert "WARN" in result.output
        assert "freshnes" in result.output

    def test_check_strict_fails(self, tmp_path):
        result = self.runner.invoke(app, ["check", "--contract", str(self._write(tmp_path)), "--strict"])
        assert result.exit_code == 1
        assert "freshnes" in result.output

    def test_validate_prints_the_warning(self, tmp_path, tmp_parquet):
        path = tmp_path / "c.yaml"
        path.write_text(CONTRACT_WITH_TYPO.replace("/tmp/x.parquet", tmp_parquet.as_posix()))
        result = self.runner.invoke(app, ["validate", "--contract", str(path)])
        assert result.exit_code == 0
        assert "Warning: DataContract: unknown key(s) ['freshnes']" in result.output
