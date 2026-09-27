"""Integration tests for the Akad Registry REST API.

Uses an in-memory SQLite database (no PostgreSQL required).
The registry_client fixture from conftest.py wires everything up.
"""
from __future__ import annotations

from datetime import UTC, datetime


class TestHealthEndpoint:
    def test_health_returns_ok(self, registry_client):
        resp = registry_client.get("/health/")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestContractPublish:
    def _payload(self, name="test_contract", version="1.0.0"):
        return {
            "name":    name,
            "version": version,
            "content": {
                "apiVersion": "datacontract/v1",
                "kind":       "DataContract",
                "metadata":   {
                    "name": name, "version": version,
                    "owner": {"team": "T", "email": "t@t.com"},
                },
                "dataset":   {"format": "parquet", "location": "/tmp/x.parquet"},
                "on_breach": "warn",
            },
        }

    def test_publish_returns_201(self, registry_client):
        resp = registry_client.post("/contracts/", json=self._payload())
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test_contract"
        assert data["version"] == "1.0.0"
        assert data["is_current"] is True

    def test_publish_new_version_marks_old_as_not_current(self, registry_client):
        registry_client.post("/contracts/", json=self._payload("my_contract", "1.0.0"))
        registry_client.post("/contracts/", json=self._payload("my_contract", "2.0.0"))

        resp = registry_client.get("/contracts/my_contract")
        assert resp.status_code == 200
        assert resp.json()["version"] == "2.0.0"

    def test_republishing_identical_version_is_a_noop(self, registry_client):
        first = registry_client.post("/contracts/", json=self._payload("dupe", "1.0.0"))
        again = registry_client.post("/contracts/", json=self._payload("dupe", "1.0.0"))

        assert again.status_code == 200
        assert again.json()["id"] == first.json()["id"]
        assert len(registry_client.get("/contracts/dupe/versions").json()) == 1

    def test_republishing_version_with_different_content_is_rejected(self, registry_client):
        registry_client.post("/contracts/", json=self._payload("dupe", "1.0.0"))
        changed = self._payload("dupe", "1.0.0")
        changed["content"]["on_breach"] = "fail"

        resp = registry_client.post("/contracts/", json=changed)

        assert resp.status_code == 409
        assert "immutable" in resp.json()["detail"]
        stored = registry_client.get("/contracts/dupe/versions/1.0.0").json()
        assert stored["content"]["on_breach"] == "warn"

    def test_non_semver_version_is_rejected(self, registry_client):
        resp = registry_client.post("/contracts/", json=self._payload("loose", "1.0"))
        assert resp.status_code == 422

    def test_prerelease_semver_is_accepted(self, registry_client):
        resp = registry_client.post("/contracts/", json=self._payload("pre", "2.0.0-rc.1"))
        assert resp.status_code == 201

    def test_version_must_match_contract_metadata(self, registry_client):
        payload = self._payload("mismatch", "1.0.0")
        payload["version"] = "1.0.1"
        resp = registry_client.post("/contracts/", json=payload)
        assert resp.status_code == 422

    def test_exactly_one_current_version_after_several_publishes(self, registry_client):
        for version in ("1.0.0", "1.1.0", "2.0.0"):
            registry_client.post("/contracts/", json=self._payload("multi", version))

        versions = registry_client.get("/contracts/multi/versions").json()
        assert [v["version"] for v in versions if v["is_current"]] == ["2.0.0"]


class TestContractConstraints:
    """The database itself enforces immutability, independent of the API."""

    def _session(self, tmp_path):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from registry.database import Base

        engine = create_engine(f"sqlite:///{tmp_path / 'constraints.db'}")
        Base.metadata.create_all(engine)
        return Session(engine)

    def _record(self, version, *, current):
        from registry.models import ContractRecord
        return ContractRecord(name="c", version=version, content="{}", is_current=current)

    def test_duplicate_version_violates_unique_index(self, tmp_path):
        import pytest
        from sqlalchemy.exc import IntegrityError

        with self._session(tmp_path) as db:
            db.add_all([self._record("1.0.0", current=False), self._record("1.0.0", current=False)])
            with pytest.raises(IntegrityError):
                db.commit()

    def test_second_current_version_violates_unique_index(self, tmp_path):
        import pytest
        from sqlalchemy.exc import IntegrityError

        with self._session(tmp_path) as db:
            db.add_all([self._record("1.0.0", current=True), self._record("2.0.0", current=True)])
            with pytest.raises(IntegrityError):
                db.commit()

    def test_many_non_current_versions_are_allowed(self, tmp_path):
        with self._session(tmp_path) as db:
            db.add_all([self._record(v, current=False) for v in ("1.0.0", "1.1.0", "1.2.0")])
            db.add(self._record("2.0.0", current=True))
            db.commit()


class TestContractRetrieval:
    def _publish(self, client, name="api_contract", version="1.0.0"):
        client.post("/contracts/", json={
            "name": name, "version": version,
            "content": {"apiVersion": "datacontract/v1", "kind": "DataContract",
                        "metadata": {"name": name, "version": version,
                                     "owner": {"team": "T", "email": "t@t.com"}},
                        "dataset": {"format": "parquet", "location": "/tmp/x.parquet"},
                        "on_breach": "warn"},
        })

    def test_list_returns_current_contracts(self, registry_client):
        self._publish(registry_client, "c1")
        self._publish(registry_client, "c2")
        resp = registry_client.get("/contracts/")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert "c1" in names and "c2" in names

    def test_get_by_name(self, registry_client):
        self._publish(registry_client, "named_contract")
        resp = registry_client.get("/contracts/named_contract")
        assert resp.status_code == 200
        assert resp.json()["name"] == "named_contract"

    def test_get_nonexistent_returns_404(self, registry_client):
        resp = registry_client.get("/contracts/does_not_exist")
        assert resp.status_code == 404

    def test_list_versions(self, registry_client):
        self._publish(registry_client, "versioned", "1.0.0")
        self._publish(registry_client, "versioned", "2.0.0")
        resp = registry_client.get("/contracts/versioned/versions")
        assert resp.status_code == 200
        versions = [c["version"] for c in resp.json()]
        assert "1.0.0" in versions and "2.0.0" in versions


class TestValidationResults:
    def _result_payload(self, contract_name="test", status="COMPLIANT"):
        return {
            "contract_name":    contract_name,
            "contract_version": "1.0.0",
            "dataset_location": "/tmp/x.parquet",
            "validated_at":     datetime.now(UTC).isoformat(),
            "overall_status":   status,
            "row_count":        100,
            "clause_results":   [],
            "error_message":    None,
        }

    def test_store_result_returns_201(self, registry_client):
        resp = registry_client.post("/validation-results/", json=self._result_payload())
        assert resp.status_code == 201
        data = resp.json()
        assert data["overall_status"] == "COMPLIANT"

    def test_list_results(self, registry_client):
        registry_client.post("/validation-results/", json=self._result_payload("c1", "COMPLIANT"))
        registry_client.post("/validation-results/", json=self._result_payload("c1", "BREACH"))
        resp = registry_client.get("/validation-results/?contract_name=c1")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_result_by_id(self, registry_client):
        post_resp = registry_client.post("/validation-results/", json=self._result_payload())
        rid = post_resp.json()["id"]
        resp = registry_client.get(f"/validation-results/{rid}")
        assert resp.status_code == 200

    def test_get_nonexistent_result_returns_404(self, registry_client):
        resp = registry_client.get("/validation-results/99999")
        assert resp.status_code == 404

    def test_list_with_limit(self, registry_client):
        for i in range(5):
            registry_client.post("/validation-results/", json=self._result_payload(f"c{i}"))
        resp = registry_client.get("/validation-results/?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()) == 2
