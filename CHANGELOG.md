# Changelog

All notable changes to this project are documented here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/), versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- **Typos in contracts no longer disappear silently.** A contract key Akad doesn't recognise (such as `freshnes:` or `max_null_percentag:`) used to be dropped without a word, so the rule it declared wasn't enforced and `akad check` still reported the contract as valid. Loading such a contract now emits an `UnknownContractKeyWarning` naming the key and the section it's in. `akad check` and `akad validate` print these warnings, and `akad check --strict` fails on them. Unknown keys will be an error in the next major version.
- **Error notifications are no longer labelled as breaches.** When a contract couldn't be evaluated, the webhook event was `DATA_CONTRACT_BREACH` and the email said `[Akad BREACH]` with "Failed Clauses (0)" and no reason. The webhook event is now `DATA_CONTRACT_ERROR` for errors, and the payload adds `overall_status`, `error_message` and `errored_clauses`. Each clause now also carries its `status`. The email subject and body name the status and include the error.
- **`akad history` escapes the contract name.** It was pasted into the query string as-is, so a name containing `&` or a space asked for the wrong thing. `akad list` and `akad history` now go through `RegistryClient`.
- **`contract_to_yaml_dict` keeps every section.** It copied fields by hand and dropped freshness, notifications, consumers, tags and descriptions.
- The registry's detail endpoints no longer overwrite the database row's JSON text with a parsed dict to build the response.

### Added
- `RegistryClient.list_contracts()` and `RegistryClient.list_validation_results(contract_name=None, limit=50)`.

### Removed
- Contract fields that were accepted but never used: `dataset.catalog_uri`, `dataset.catalog_type`, `dataset.namespace` and `consumers[].slack_webhook`. Contracts that set them still load, with an unknown-key warning.

## [2.0.0] - 2026-09-27

Only registry deployments are affected. Pipelines that validate without a registry need no changes. See [Upgrading from 1.x](https://parmenidessartre.github.io/Akad/registry/#upgrading-from-1x) for the rollout order.

### Breaking (registry)
- **Registry writes need an API token.** `POST /contracts/` and `POST /validation-results/` require `Authorization: Bearer <token>`, checked against `AKAD_API_TOKENS` (comma-separated). With no tokens configured, writes are refused. Clients send `$AKAD_API_TOKEN`, or `--token` on the CLI, or `registry_token=` on `DataContractValidator`. Set `AKAD_REGISTRY_READS_REQUIRE_AUTH=true` to require a token for reads too. A pipeline posting a result without a valid token still completes its run, with a warning.
- **Published contract versions are immutable.** Re-publishing identical content returns `200` without creating a row. Different content under an existing version returns `409`. Versions must be semver, and must match the contract's `metadata`.

### Fixed
- Two concurrent publishes of the same contract could both end up current. One current version per contract is now enforced by a database index.

### Added
- Alembic schema migrations, run automatically on registry startup. Databases created by earlier registries are adopted in place. If they contain the same version published twice, the migration stops, lists the duplicates, and deletes nothing. On PostgreSQL, a lock makes simultaneous startups (several workers or replicas) take turns.
- `RegistryClient.publish_contract` returns `True` when it created the version and `False` for an identical re-publish. `akad publish` reports the latter as "Already published … nothing changed".
- "Running the Registry" docs page covering auth, versions and migrations.
- CI runs the migration tests against PostgreSQL as well as SQLite.

## [1.4.0] - 2026-09-27

### Fixed
- **Validation fails closed.** When the contract couldn't be evaluated (unreadable dataset, erroring rule) the result is `ERROR`. Previously that passed silently: `akad validate` exited `0` and the SDK returned normally even under `on_breach: fail`. Now `on_breach: fail` raises the new `DataContractEvaluationError`, and `akad validate` exits `2` in either mode and prints the reason. `--output json` gains `error_message` and `errored_clauses`.
- **`decimal` and `date` columns from Parquet now pass their type checks.** pyarrow reads `decimal128` and `date32` into pandas as object columns of `Decimal` / `date` values, which always failed. Arrow-backed decimal columns (`pd.ArrowDtype`) are accepted too, and `akad infer` now infers `decimal` and `date` for these columns instead of `string`.
- **`akad publish` no longer reports success when publishing failed.** `RegistryClient.publish_contract` raises on connection errors and non-2xx responses instead of logging a warning, and the CLI exits `1` with the registry's response. `post_validation_result` still only warns, so a registry outage never breaks a pipeline run.

### Added
- `DataContractError` base class (exported from `akad`), with `DataContractBreachError` and `DataContractEvaluationError` as subclasses. Existing `except DataContractBreachError` handlers keep working.
- `ValidationResult.errored_clauses`.

## [1.3.0] - 2026-06-24

### Added
- `business_rules` contract section — cross-column and conditional checks that column-level Schema/Quality rules can't express (e.g. `status != 'COMPLETED' or ship_date.notnull()`, `end_date >= start_date`). Backed by pandas' own restricted expression evaluator (`df.eval(..., engine="python")`), not Python's `eval()` — no access to builtins, imports, or arbitrary function calls. A malformed expression becomes an `ERROR` clause rather than crashing the run.
- `akad diff` now understands `business_rules`: a removed rule is breaking, an added rule is non-breaking, and a changed expression is conservatively always breaking (strictness can't be inferred statically from arbitrary code).
- `akad.profiler.contract_to_yaml_dict()` now also serializes `business_rules` when present, for round-trip completeness (note: `akad infer` itself never generates business rules — there's no reliable way to derive cross-column logic from a data sample).

## [1.2.1] - 2026-06-23

Code-quality hardening pass — no new features, no behavior changes for existing users.

### Changed
- Expanded the `ruff` lint ruleset to include security (`S`), performance (`PERF`), unused-argument (`ARG`), complexity (`C901`), naming (`N`), pathlib (`PTH`), and ruff-specific (`RUF`) checks, with a per-path exception for `tests/` where bare `assert` and placeholder `/tmp/...` paths are the established idiom, not a real risk
- `DataContract.api_version` renamed from `apiVersion` (still aliased to the `apiVersion` YAML key — the contract format on disk is unchanged) for consistency with every other snake_case field, mirroring the existing `schema_`/`schema` alias
- `SQLReader.get_last_modified()` now quotes the dynamic column/table identifiers through SQLAlchemy's own `identifier_preparer` instead of raw f-string interpolation, and gained an explicit guard for a missing `table_name`
- Reduced `akad cli.py`'s `diff` command below the complexity threshold by extracting its argument-validation/loading logic into `_load_diff_contracts()`
- Various conciseness fixes surfaced by the expanded linting: comprehensions instead of manual append loops in `akad.differ` and the email notifier, `next()` instead of a single-element list slice in tests, dead `contract` parameter removed from `_build_email_body()`

## [1.2.0] - 2026-06-23

### Added
- `akad diff` CLI command — compares two contract versions (two local files, or two versions already published to the registry) and classifies every change as breaking or non-breaking for a consumer relying on the old contract
- `akad.differ` module exposing `diff_contracts()` for programmatic use
- `RegistryClient.get_contract_version(name, version)` — fetch a specific historical contract version, not just the current one

### Fixed
- CLI commands using the ✓/✗ status icons (`validate`, `check`, `history`, `diff`) could crash with `UnicodeEncodeError` on a non-UTF-8 console (e.g. the `cp1252` default on many Windows setups); output is now forced to UTF-8

## [1.1.0] - 2026-06-23

### Added
- `akad infer` CLI command — profiles an existing dataset (Parquet or SQL) and scaffolds a starter contract YAML, with inferred schema, volume, and quality rules
- `akad.profiler` module exposing `profile_dataframe()` and `generate_contract()` for programmatic use

## [1.0.0] - 2026-06-23

Initial release as `akad-framework`, renamed from the earlier `datacontract-framework` prototype.

### Added
- Core validation engine: schema, freshness, volume, and quality checks against Parquet and SQL datasets
- `DataContractValidator` SDK with file-based and registry-based contract loading
- FastAPI contract registry with PostgreSQL/SQLite backend
- FastAPI + Jinja2 + Tailwind observability dashboard
- Webhook and email breach notifiers
- `akad` CLI: `check`, `publish`, `validate`, `list`, `history`
- CI/CD pipeline: tests + lint (ruff) + type checks (mypy) on every push, tag-triggered PyPI publish via Trusted Publishing
- MkDocs Material documentation site
