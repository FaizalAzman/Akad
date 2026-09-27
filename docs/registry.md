# Running the Registry

The registry stores contract versions and validation history. This page covers what an operator needs: authentication, how versions behave, and schema migrations.

## Upgrading from 1.x

Version 2.0 changes the registry in two ways that existing deployments must plan for: **writes need an API token**, and **published versions can't be changed**. Pipelines that validate without a registry are unaffected.

Upgrade in this order. A 1.x client ignores `AKAD_API_TOKEN` and a 1.x registry ignores the `Authorization` header, so doing clients first means there's never a window where pipelines lose their results.

1. **Back up the registry database.**
2. **Check for duplicate versions.** Registries before 2.0 let a version be published twice, and 2.0's startup migration stops if it finds any, without deleting anything:

    ```sql
    SELECT name, version, COUNT(*) FROM contracts GROUP BY name, version HAVING COUNT(*) > 1;
    ```

    For each duplicate, keep the authoritative row and delete the others (see [below](#if-startup-stops-with-published-more-than-once)).
3. **Generate tokens.** Create one per client group, such as CI and Airflow.
4. **Give every client its token as `AKAD_API_TOKEN`.** That covers CI jobs that run `akad publish` or `akad validate --registry-url`, Airflow workers and other pipelines that pass `registry_url` to `DataContractValidator`, and the dashboard (only needed if you'll require auth for reads).
5. **Upgrade clients to 2.0:** `pip install -U akad-framework`.
6. **Upgrade the registry,** starting it with `AKAD_API_TOKENS` set to the tokens from step 3. The schema migration runs automatically on startup.
7. **Check it:** `curl <registry>/health/`, then `akad publish` a contract from CI. A pipeline run should appear in `akad history`.

After the upgrade, a CI job that re-publishes an **unchanged** contract keeps working, because an identical re-publish is a no-op. A job that changes a contract **without bumping its version** now fails with `409`. That's intended: bump the version.

## Authentication

Every write needs a bearer token. That covers publishing a contract (`POST /contracts/`) and posting a validation result (`POST /validation-results/`). Reads are open by default.

| Where | Variable | Meaning |
|---|---|---|
| Registry | `AKAD_API_TOKENS` | Comma-separated list of accepted tokens, e.g. one for CI and one for Airflow. **With none set, every write is refused**: the registry fails closed rather than accepting anonymous publishes. |
| Registry | `AKAD_REGISTRY_READS_REQUIRE_AUTH` | Set to `true` to require a token for reads as well. `/health/` always stays open. |
| Clients | `AKAD_API_TOKEN` | The token the CLI, SDK and dashboard send. Pass `--token` to the CLI or `registry_token=` to `DataContractValidator` to override it. |

```bash
# registry
AKAD_API_TOKENS="ci-7f3a...,airflow-91bc..." uvicorn registry.main:app

# clients
export AKAD_API_TOKEN=ci-7f3a...
akad publish --contract contracts/sales.yaml --registry-url https://akad-registry.internal
```

Generate tokens with something like `python -c "import secrets; print(secrets.token_urlsafe(32))"`, and keep them in your secrets manager rather than in files. To rotate one, add the new token, move clients across, then remove the old token and restart.

A pipeline that posts a result without a valid token still finishes its run: the SDK logs a warning and carries on, the same as when the registry is unreachable. Publishing a contract without a valid token fails, and `akad publish` exits `1`.

## Contract versions

- **Published versions are immutable.** A given `name` + `version` can only ever hold one piece of content.
- **Re-publishing identical content is a no-op** (HTTP `200`), so a CI job that publishes on every merge can safely re-run.
- **Re-publishing different content under an existing version is rejected** (HTTP `409`). Bump the version instead.
- **Versions must be semver:** `MAJOR.MINOR.PATCH`, optionally with a pre-release or build suffix such as `2.0.0-rc.1`. The `name` and `version` in the request must match the contract's own `metadata`.
- **The most recently published version is the current one**, meaning the version `DataContractValidator(contract_name=...)` resolves. Exactly one version per contract is current. The database enforces this, so two simultaneous publishes can't both become current: the loser gets a `409` and can retry.

Every published version remains available at `/contracts/{name}/versions/{version}`, which is what `akad diff --name` compares.

## Schema migrations

The registry migrates its database schema automatically on startup, using Alembic.

- **Fresh databases** are created at the latest schema.
- **Databases created by registry 1.4 or earlier** (before migrations existed) are adopted in place: they're stamped at the baseline and upgraded, keeping all data.
- **Several workers or replicas starting at once** are safe on PostgreSQL. A database lock makes them take turns, and later starters find the schema already current. SQLite is for single-process local development only.

### If startup stops with "published more than once"

Registries before 1.5 allowed the same version to be published twice. Those rows may hold different content, so the migration won't guess which is authoritative, and it never deletes data. It stops and lists the duplicates with their row ids:

```
RuntimeError: Cannot make contract versions immutable: these versions were
published more than once: daily_sales v1.0.0 (row ids 4, 9). ...
```

Inspect them with `SELECT id, published_at, content FROM contracts WHERE id IN (4, 9)`, delete the rows that aren't authoritative, and restart the registry.

### Developing migrations

`alembic.ini` at the repository root points the Alembic CLI at `registry/migrations`. It uses `DATABASE_URL`, like the registry:

```bash
DATABASE_URL=postgresql://akad:...@localhost:5432/akad uv run alembic revision -m "describe the change"
DATABASE_URL=postgresql://akad:...@localhost:5432/akad uv run alembic upgrade head
```

The migration tests run against SQLite, and against PostgreSQL too when `AKAD_TEST_POSTGRES_URL` points at a disposable database (CI does this). One test checks that the migrated schema matches the SQLAlchemy models exactly, so a model change without a migration fails the build.
