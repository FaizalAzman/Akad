# Product Review & Roadmap

*Status: proposal · Last reviewed: 2026-09-27 · Baseline: akad-framework v1.3.0*

This document answers three questions:

1. **Is Akad worth investing in as a product, or should it stay a personal tool?**
2. **If we build it, what does it have to become to live inside a real data platform** (orchestrators, catalogs, lineage, warehouses, lakehouses), rather than standing alone?
3. **In what order, and what does "done" look like at the level of a commercial platform?**

---

## 1. Verdict

**Build it all the way, to the engineering bar of a commercial data platform (Databricks, Snowflake, Confluent), whether or not anyone has adopted it yet.** Position it as the interoperable contract layer for regulated financial data, starting with Islamic finance and ASEAN regulatory reporting, rather than as yet another general-purpose data-quality tool.

"Databricks level" here means Databricks-grade **engineering and operability** for a data-contract product: scale, reliability, security, API stability, deployability and documentation. It does **not** mean rebuilding a compute platform. Akad runs *on* engines like Databricks, Snowflake and DuckDB, and never replaces them. The bar is written down in [§6 Definition of done](#6-definition-of-done-platform-grade-bar-and-phase-gates), and every phase is measured against it.

The codebase is clean, well tested (231 tests, strict lint and mypy) and the core idea is sound. But as a *horizontal* "data contract framework" it arrives late and behind:

| | Akad today | Where the market is |
|---|---|---|
| Contract format | Own `datacontract/v1` schema | The industry is standardising on **ODCS** (Open Data Contract Standard, Bitol / Linux Foundation). `datacontract-cli` has already moved to it. |
| Execution | Loads the full dataset into pandas | Checks are pushed down to the warehouse (SQL, Spark, DuckDB) and scale to TB-sized tables |
| Connectors | Local/S3 Parquet, SQLAlchemy | Snowflake, BigQuery, Databricks, Iceberg, Delta, Kafka and more |
| Ecosystem | Python SDK + own registry/dashboard | OpenLineage events, catalog sync (DataHub, OpenMetadata, Atlan), dbt, Dagster asset checks |

Competing head-on with `datacontract-cli`, Soda, Great Expectations and dbt tests on generic features is a losing race for a small team. Two things Akad already does well point to a gap those tools leave:

- **`business_rules`**: cross-column invariants of the kind that caused the BNM NPF misreporting story in [the worked example](examples.md). Regulators care about these, and generic tools treat them as an afterthought.
- **The name and domain.** *Akad* is the Islamic finance term for contract. Nobody owns "data contracts for regulated finance" in Malaysia and ASEAN: BNM reporting, MFRS 9 staging, Shariah-compliance invariants, and audit-grade evidence of which checks ran on which data.

So the plan is to **commoditise the parts everyone has** by adopting the open standards (ODCS, OpenLineage, SQL pushdown) instead of reinventing them, and **differentiate on the part nobody has**: regulated-finance rule packs, a governance control plane, and verifiable evidence.

### Target end state (v3.0)

- **Spec:** native ODCS v3 with full round-trip, a published JSON Schema, and an `akad lint` language server for IDEs.
- **Engine:** one rule IR compiled to Databricks/Spark, Snowflake, BigQuery, Trino, Postgres, DuckDB (Parquet, Iceberg, Delta) and pandas/Polars, proven identical by a conformance suite. Validates billion-row tables with constant client memory.
- **Control plane:** highly available, multi-tenant, SSO/SCIM, RBAC, immutable audit log, signed evidence, publish-time breaking-change gates, consumer subscriptions, SLOs and anomaly baselines.
- **Ecosystem:** first-class Airflow provider, Dagster, dbt, Prefect, Databricks Workflows, Unity Catalog, OpenLineage, DataHub, OpenMetadata, Prometheus/OTel, and a Terraform provider and Helm chart.
- **Product surface:** a versioned REST API with a deprecation policy, Python and Java/Scala SDKs, a CLI, a real web UI, docs with tutorials and reference, and published benchmarks.

Adoption is tracked (see §6) but does not decide whether an active phase gets built; engineering quality gates do. The platform-service phases (3 and 5) are deferred until there is demand for them (see [Current focus](#current-focus)).

---

## 2. Current state: honest assessment

### Strengths
- A small, readable core (~2.2k LOC) with a clear split: `models` → `readers` → `validators` → `engine` → `sdk`.
- Good developer experience: `validate_dataframe()` for unit tests, injectable clients, and a plugin validator API.
- `akad diff` with breaking and non-breaking classification is the most product-grade feature. The rule "loosening a guarantee is breaking" is correct.
- `akad infer` lowers the cost of writing the first contract.
- Split dependency extras (`core`, `registry`, `dashboard`).

### Correctness bugs (confirmed, fix before anything else)

| # | Issue | Where | Impact |
|---|---|---|---|
| B1 | **The gate fails open when the dataset can't be read.** An unreadable or missing dataset produces `ERROR`. `akad validate` exits `0` and the SDK does not raise, even with `on_breach: fail`. | `akad/sdk.py:78`, `akad/cli.py:49` | A pipeline "passes" validation on data it never saw. This is the worst possible failure for a contract gate. |
| B2 | **`decimal` and `date` columns always fail type checks on real Parquet.** PyArrow's `decimal128` and `date32` arrive in pandas as `object` dtype, but the checks expect float or datetime64. | `akad/validators/schema_validator.py:32-34` | Money columns are exactly what a finance user declares as `decimal`. The flagship use case fails on day one. |
| B3 | **`akad publish` reports success when publishing fails.** `RegistryClient.publish_contract` logs and swallows every error, and the CLI then prints `Published …`. | `akad/registry_client.py:54`, `akad/cli.py` `publish` | CI believes a contract was registered when it wasn't. |
| B4 | **Registry versions are mutable and can be duplicated.** Nothing stops a second publish of `1.0.0`. There is no unique constraint on `(name, version)`, and the "current" flip is not atomic. | `registry/models.py`, `registry/routers/contracts.py` | A contract version is no longer a stable reference, which undermines `diff` and audit. |
| B5 | **The registry has no authentication.** Anyone who can reach it can publish or overwrite contracts and post fake results. | `registry/` | Blocks any shared or production deployment. |

### Structural gaps
- **Scale.** `ParquetReader` reads the whole file and `SQLReader` calls `read_sql_table` on the whole table (`akad/readers/*.py`). Anything above a few GB will run out of memory, and the approach moves data to the check instead of the check to the data.
- **Half-built model fields.** `consumers` (`ConsumerSpec`) and `catalog_uri`, `catalog_type`, `namespace` exist in the model but nothing uses them.
- **Static thresholds only.** Validation history is stored but never used as a baseline, for example "row count dropped 40% compared with the trailing 7 days".
- **No row-level evidence.** A failure reports a count, not the offending keys, and there is no sample or quarantine output.
- **Weak type system.** `date` and `timestamp` are indistinguishable. There is no precision or scale for decimal and no timezone semantics.
- **Python ≥3.12 floor.** Many managed Airflow environments (MWAA, Composer) still run 3.10 or 3.11, which blocks the core library on the workers it targets.
- **The registry uses `create_all`** even though `alembic` is a dependency, so upgrades have no migration path.

---

## 3. Product positioning

**Target user (primary):** data or analytics engineers at banks, takaful operators, fintechs and asset managers in Malaysia and ASEAN, who own regulatory and financial reporting pipelines.

**Target user (secondary):** risk, compliance and internal-audit teams, who consume evidence rather than write YAML.

**Job to be done:** *"Prove, every run, that the data we report and consume meets its agreed obligations. Catch the moment it stops, before the regulator or a downstream team does."*

**Positioning statement:** Akad is the open, standards-based data contract layer for regulated finance. You write contracts in ODCS, enforce them inside your existing pipelines and warehouse, and get audit-grade evidence of every check.

**Design principles**
1. **Standards in, standards out.** ODCS for contracts, OpenLineage for events, SQL for execution. No lock-in.
2. **Embed, don't replace.** Akad runs inside Airflow, Dagster, dbt and Spark jobs. It never becomes the scheduler, the catalog or the BI tool.
3. **Push the check to the data.** Never pull a table into Python when the warehouse can answer the question.
4. **Fail closed.** If the contract can't be evaluated, that is a failure, not a pass.
5. **Evidence is a product feature.** Every result is reproducible: which contract version, which data snapshot, which rule, and which rows.

---

## 4. Target architecture: Akad in the data platform

Akad splits into three layers, each usable alone:

```
                 ┌──────────────────────────── Contract sources ────────────────────────────┐
                 │  Git (YAML, ODCS)   dbt manifest   Schema Registry (Avro/Proto)   Catalogs │
                 └──────────────────────────────────────┬───────────────────────────────────┘
                                                        │ import / sync
┌──────────────────────────── 1. SPEC ──────────────────▼──────────────────────────────────────┐
│  ODCS v3 contracts (+ `x-akad` extensions for business rules, regulatory packs)             │
│  akad lint · akad diff · akad infer · import/export (dbt, JSON Schema, Avro, SQL DDL)        │
└──────────────────────────────────────┬───────────────────────────────────────────────────────┘
                                       │
┌──────────────────────────── 2. ENGINE (embeddable library) ─────────────────────────────────┐
│  Rule compiler → backends:  SQL pushdown (Snowflake, BigQuery, Databricks, Postgres, Trino)│
│                             DuckDB (Parquet, Iceberg, Delta, S3/GCS/ADLS)                   │
│                             Spark (PySpark DataFrame)   pandas/Polars (small / tests)       │
│  Orchestrator adapters:     Airflow provider · Dagster asset checks · Prefect · dbt hook    │
└──────────────────────────────────────┬───────────────────────────────────────────────────────┘
                                       │ results + evidence
┌──────────────────────────── 3. CONTROL PLANE (optional service) ────────────────────────────┐
│  Registry: immutable versions, auth/RBAC, publish-time breaking-change gate, approvals       │
│  Consumers & subscriptions · SLAs · baselines/anomaly · evidence store (signed, retained)    │
└───────┬───────────────┬────────────────────┬──────────────────────┬─────────────────────────┘
        ▼               ▼                    ▼                      ▼
   OpenLineage     Catalog sync         Metrics                 Alerting
   (Marquez,       (DataHub,            (Prometheus /           (Slack, Teams,
    DataHub,        OpenMetadata,        OpenTelemetry)          PagerDuty,
    Atlan)          Atlan, Unity)                                Opsgenie, email)
```

### Integration matrix (what "not standalone" means concretely)

| Category | Integration | Why it matters | Phase |
|---|---|---|---|
| **Spec** | ODCS v3 import/export (lossless round-trip) | Contracts written elsewhere run in Akad, and contracts written in Akad can be used elsewhere | 1 |
| | dbt `manifest.json` import, dbt model contract export | Most analytics teams already declare schemas in dbt | 1 |
| | Avro and Protobuf from Confluent or Apicurio schema registry | Contracts on streaming sources | 3 |
| **Execution** | Airflow provider (`AkadCheckOperator`, deferrable, XCom result, OpenLineage facets) | Replaces the hand-rolled `raise ValueError` in the README | 1 |
| | Dagster asset checks (`@akad_asset_check`) | First-class in Dagster's UI and lineage | 1 |
| | dbt post-hook or `dbt run-operation` | Validate the model dbt just built | 2 |
| | Prefect task, GitHub Action, pre-commit hook | CI and lightweight orchestrators | 1 |
| | Spark / Databricks (PySpark DataFrame backend, Unity Catalog tables) | Lakehouse users | 2 |
| **Storage** | SQL pushdown via SQLGlot (dialect-portable) | Scale, no data movement | 2 |
| | DuckDB for Parquet, Iceberg (`pyiceberg`), Delta (`delta-rs`) on S3/GCS/ADLS | Lakehouse tables without a warehouse | 2 |
| | Partition-scoped validation (`--partition dt=2026-09-27`) | Validate only what landed, not the entire history | 2 |
| **Observability** | OpenLineage `DataQualityAssertions` facet emitted per run | Results appear in Marquez, DataHub, Atlan and Airflow lineage for free | 1 |
| | Catalog push (DataHub assertions, OpenMetadata test results) | Contract status appears where people already look | 2 |
| | Prometheus `/metrics` and OTel spans | Standard SRE dashboards and alerting (Grafana) | 2 |
| | Results sink to a warehouse table | Analysts can query quality history with SQL | 2 |
| **Alerting** | Consumer-routed notifications (uses the existing `consumers` field), dedup and escalation | The right team is told, once | 3 |
| **APIs** | Versioned REST API with an OpenAPI spec, webhooks on publish/breach, token auth | Other systems can build on Akad | 1 (auth), 3 (webhooks) |

**Explicit non-goals.** Akad will not have its own scheduler, its own catalog, its own lineage graph, a general BI dashboard, or its own ML anomaly platform. The existing dashboard stays a lightweight operator view. Rich visualisation belongs in catalogs and Grafana.

---

## 5. Roadmap

Phases are sequential in priority, not strictly in time. Every phase ends with a **gate** (§6).

### Current focus

**Active: Phases 0, 1, 2 and 4. Deferred: Phases 3 and 5.**

The active phases produce a complete, credible tool for a data team at a bank or fintech: correct results, it fits into their orchestrator and lineage tooling, checks run inside their warehouse, and it ships regulated-finance rule packs with audit evidence. Phases 3 and 5 turn Akad into a multi-team platform service, which only pays off once several teams depend on it.

Phases 3 and 5 are reactivated when **either** of these holds:
- at least three teams run Akad in production and ask for shared governance (consumer sign-off, SSO, multi-tenancy), or
- Akad is taken forward as a commercial or consulting product.

Within the active phases, priority follows what banks in the region actually run: **Airflow** first among orchestrators, and **Databricks/Spark plus one enterprise RDBMS (Oracle or SQL Server)** first among warehouses.

### Phase 0: Credibility (≈ 2–3 weeks) · *"Correct before clever"*
Goal: nothing in the current product gives a wrong answer.

- [x] **B1**: Treat `ERROR` as a failure: SDK raises `DataContractEvaluationError` under `on_breach: fail`, and `akad validate` exits `2`.
- [ ] An explicit `on_error: fail|warn` setting, so `on_breach: warn` contracts can still fail closed when the data can't be evaluated.
- [x] **B2** (immediate fix): `decimal` and `date` columns read from Parquet pass their type checks, and `akad infer` detects them.
- [ ] A type system that understands Arrow: check against the Arrow schema before converting to pandas. Distinguish `date` from `timestamp`, support `decimal(p,s)`, and handle timezones.
- [x] **B3**: `publish_contract` raises on failure. The CLI exits non-zero and prints the registry's error.
- [x] **B4**: Unique `(name, version)`, `409 Conflict` on republishing changed content (identical content is a no-op), one current version per contract enforced by the database, semver validation.
- [x] **B5**: Token-based auth on registry writes (API keys), with reads open by default and optionally authenticated.
- [x] Alembic migrations in place of `create_all`, run on startup, adopting pre-migration databases in place.
- [ ] Lower the core Python floor to 3.10 (keep 3.12 for the registry and dashboard if needed).
- [ ] Remove or implement the dead model fields (`catalog_*`, `consumers`) and document what exists.

**Exit criteria:** every bug above has a regression test, and a test suite runs a real Parquet file with decimal, date and timestamp columns end to end.

### Phase 1: Interoperability (≈ 6–8 weeks) · *"Speak the ecosystem's language"*
Goal: a team already using Airflow, Dagster, dbt or a catalog can adopt Akad without changing how they work.

- [ ] **ODCS v3 as the native format.** Read and write ODCS, keep `datacontract/v1` as a deprecated input with `akad migrate`, and put Akad-specific rules under `x-akad` or ODCS `quality` custom rules.
- [ ] **`akad diff` on ODCS**, and as a GitHub Action that comments on PRs with breaking changes.
- [ ] **OpenLineage emitter.** Each validation emits a run event with a `DataQualityAssertions` facet.
- [ ] **Airflow provider package** (`apache-airflow-providers-akad` style): operator, deferrable sensor, connection type, lineage integration.
- [ ] **Dagster integration** *(lower priority)*: contract → asset checks factory.
- [ ] **dbt import** *(lower priority)*: `akad import dbt --manifest target/manifest.json`.
- [ ] **Structured results schema** (JSON Schema published, versioned) so downstream tools can depend on it.
- [ ] Row-level evidence: each failed clause carries the violating row count, a capped sample of primary keys, and the SQL or expression that found them.

**Exit criteria:** the same contract runs from the CLI and Airflow, and its results appear in Marquez or DataHub with no custom glue.

### Phase 2: Scale (≈ 8–10 weeks) · *"Move the check, not the data"*
Goal: validate a 1 TB warehouse table or a partitioned Iceberg table in seconds to minutes, with bounded memory.

- [ ] **A rule compiler behind a backend interface**: contract clauses → an IR → SQL (via SQLGlot) / DuckDB / PySpark / pandas. Aggregate checks (null %, duplicates, min/max, row count, freshness) become a single scan query.
- [ ] **Business rules compiled to SQL** as `COUNT(*) WHERE NOT (<expr>)`, using a safe, restricted expression grammar that is shared across backends instead of relying on `df.eval` semantics.
- [ ] Connectors, in priority order: Databricks/Unity (and PySpark), Oracle or SQL Server, Postgres, Iceberg and Delta via DuckDB; then Snowflake, BigQuery and Trino. Object storage with native last-modified for freshness.
- [ ] **Partition- and increment-scoped validation** (only the newly landed data), with an option for whole-table checks on a slower cadence.
- [ ] **Quarantine / write-audit-publish support**: write failing rows to a quarantine table, or validate an Iceberg branch or Delta version before promoting it.
- [ ] Results sink table. Prometheus and OTel metrics *(lower priority)*.
- [ ] Performance benchmarks published in the docs (rows per second, memory ceiling).

**Exit criteria:** validating a 1B-row table uses constant client memory, and every built-in rule produces identical results on all backends (a cross-backend conformance test suite).

### Phase 3: Governance control plane (≈ 10–12 weeks) · *"Contracts as agreements, not just tests"*
> **Deferred** (see [Current focus](#current-focus)). Phase 0's immutable versions and API tokens cover what Phases 1, 2 and 4 depend on.

Goal: the registry becomes where producers and consumers negotiate change.

- [ ] **Publish-time breaking-change gate.** The registry runs `diff` on publish. A breaking change requires a major version bump **and** acknowledgement from registered consumers (or an explicit override with a reason, which is audited).
- [ ] **Consumer subscriptions.** Consumers register against a contract (using the existing `consumers` model). Breaches and breaking-change proposals are routed to them.
- [ ] **RBAC and SSO** (OIDC): owner, consumer, auditor and admin roles. Scopes per domain or namespace.
- [ ] **SLAs and SLOs.** Freshness and quality objectives with error budgets, not only per-run pass or fail.
- [ ] **Baselines and anomaly checks.** Use stored history for volume, null-rate and distribution drift (simple statistical methods first: z-score, seasonal median).
- [ ] **Webhooks and events** on publish, breach and deprecation. A deprecation lifecycle for contracts and columns.
- [ ] **Catalog sync** in both directions (DataHub, OpenMetadata, Atlan): ownership, glossary terms and contract status.

**Exit criteria:** a breaking change can't reach production without consumer sign-off, and every decision is in the audit log.

### Phase 4: Regulated-finance differentiation (ongoing) · *"The reason to choose Akad"*
Goal: capabilities that generic tools won't prioritise.

- [ ] **Rule packs** as versioned, installable bundles of ODCS quality rules, for example:
  - *Credit risk / MFRS 9*: stage consistency, NPF thresholds (≥ 90 DPD), impairment coverage, suspended-profit recognition.
  - *Islamic finance*: product-to-akad-type consistency (Murabahah, Ijarah, Musharakah …), profit-rate vs interest-field hygiene, Ta'widh/Gharamah late-charge rules.
  - *Regulatory reporting*: reconciliations between submission tables and source ledgers (cross-dataset rules), period completeness.
- [ ] **Cross-dataset rules**: reconciliation of totals and keys between two contracts, such as the ledger vs the regulatory extract.
- [ ] **Audit-grade evidence.** Each result records the contract hash, a data snapshot identifier (Iceberg snapshot ID, Delta version, query ID), the executed SQL and a timestamp, and is **signed** and stored under a retention policy. Evidence packs can be exported for auditors (PDF or JSON).
- [ ] **Data classification and PDPA tags** in contracts (PII, confidential), and rules that verify masking or tokenisation.
- [ ] **Control mapping.** Link contract clauses to internal control IDs and regulatory references (e.g. BNM RMiT, BCBS 239 principles) so compliance teams can report coverage.

Start the first rule pack (credit risk / MFRS 9) alongside Phase 1: it runs on the existing pandas engine and moves to SQL pushdown for free once Phase 2 lands. Cross-dataset rules and snapshot-level evidence need Phase 2's rule compiler.

**Exit criteria:** at least one design-partner institution uses a rule pack and an evidence export in an actual audit or regulatory cycle.

### Phase 5: Enterprise-grade platform (≈ 16–20 weeks) · *"Operate like Databricks"*
> **Deferred** (see [Current focus](#current-focus)).

Goal: a platform-team lead at a bank could deploy, secure and operate Akad to the same standard as their commercial vendors.

- [ ] **Deployment:** Helm chart, Terraform provider (`akad_contract`, `akad_consumer`, `akad_policy` resources), container images signed with an SBOM, and air-gapped install.
- [ ] **High availability and scale:** stateless API replicas, Postgres HA, a background worker queue for scheduled and remote runs, horizontal scaling, and load tests (sustaining ≥ 1,000 validation results/s ingest, p99 API latency < 200 ms).
- [ ] **Multi-tenancy:** workspaces or domains with isolated contracts, results and policies, and quotas per tenant.
- [ ] **Identity and security:** OIDC SSO, SCIM provisioning, fine-grained RBAC and ABAC, service principals, secrets from Vault or cloud KMS, encryption at rest and in transit, and an immutable audit log that can be exported to a SIEM.
- [ ] **Managed execution (optional):** Akad can schedule and run checks itself against registered connections (e.g. as Databricks jobs or serverless SQL), for teams without an orchestrator.
- [ ] **Web UI:** a contract editor with live lint and diff, a review and approval workflow, consumer impact view, SLO dashboards, and evidence browser. This replaces the current Jinja dashboard.
- [ ] **SDKs:** Python (primary), Java/Scala for Spark-native teams, a generated OpenAPI client, and a public API stability and deprecation policy (N-2 support).
- [ ] **Operability:** health, readiness and metrics endpoints, structured logs, runbooks, backup and restore, zero-downtime upgrades with tested migrations, and an LTS release line.
- [ ] **Compliance posture:** a controls matrix mapped to SOC 2 / ISO 27001 and BNM RMiT, a threat model, third-party pen test, and a vulnerability disclosure policy.
- [ ] **Docs and developer experience:** a docs site at the level of Databricks docs (concepts, tutorials, how-tos, API reference, versioned by release), a sandbox or demo environment, and example repos for each integration.

**Exit criteria:** the platform-grade bar in §6 passes in full.

### Illustrative timeline

For the active phases, with one maintainer working part-time:

```
2026 Q4          2027 Q1              2027 Q2              2027 Q3
├ Phase 0 ┤
          ├───── Phase 1 ─────┤
                              ├────── Phase 2 ──────┤
          ├── Phase 4: MFRS 9 pack ──┤              ├── Phase 4: cross-dataset rules, evidence ──→
```

Deferred phases (3 and 5) add roughly 12 months of work for a small team if they are reactivated.

---

## 6. Definition of done: platform-grade bar and phase gates

The finish line is **engineering quality, not adoption**. A phase counts as done only when it meets its exit criteria *and* the parts of the bar below that apply to it. Adoption is measured so the product learns from users, but it never blocks building.

### The platform-grade bar

| Dimension | Bar |
|---|---|
| **Correctness** | Fail-closed everywhere. A cross-backend conformance suite shows every rule gives identical results on every backend. Property-based and fuzz tests cover the expression grammar. ≥ 95% line coverage and mutation testing on the rule engine. |
| **Scale** | Constant client memory at any table size. A benchmark suite runs in CI on 1M, 100M and 1B rows, with results published per release, and regressions over 10% block the release. |
| **Reliability** | Control plane: 99.9% availability target, HA topology documented and tested, chaos tests for database and network failure, and the SDK degrades gracefully when the registry is down (behaviour configurable). |
| **Security** | SSO, RBAC and audit log. Signed releases with SBOM. Dependency and container scanning in CI. Third-party pen test. No known high or critical CVEs at release. |
| **API stability** | Semver across CLI, SDK, REST API and contract format. Published deprecation policy. OpenAPI and JSON Schemas versioned, with contract tests against them. |
| **Operability** | One-command install (Helm or Terraform). Zero-downtime upgrades. Backup and restore tested. Metrics, logs and traces. Runbooks. |
| **Interoperability** | ODCS round-trip passes the official schema. OpenLineage events validate against the spec. Every integration in §4 has an end-to-end test against the real system (e.g. Databricks, Snowflake and Airflow in CI via testcontainers or sandbox accounts). |
| **Documentation** | Every feature has concept, how-to and reference pages. Docs are versioned per release. Each integration has an example repo. |

### Phase gates

| Gate | Pass condition (quality) | Tracked (adoption, non-blocking) |
|---|---|---|
| Phase 0 | B1–B5 fixed with regression tests and released. | n/a |
| Phase 1 | ODCS round-trip passes. The Airflow integration has end-to-end tests. OpenLineage events are spec-valid. | Teams running Akad in an orchestrator, and PyPI downloads |
| Phase 2 | Conformance suite green on every backend. 1B-row benchmark within memory and time budgets. | Backends actually used |
| Phase 3 *(deferred)* | Breaking-change gate, consumer sign-off and audit log proven end to end. RBAC tested. | Contracts under governance |
| Phase 4 | Each rule pack has worked examples and tests against realistic synthetic data. Evidence export verified as reproducible. | Design partners |
| Phase 5 *(deferred)* | The full platform-grade bar above. | Production deployments |

**North-star metric (once there are users):** *contract-protected pipeline runs per week*, meaning runs where an Akad check executed and its result was recorded.

**Supporting metrics:**
- Time to first validated contract (target: < 15 min)
- Breaches caught before a downstream consumer noticed
- Breaking changes blocked at publish
- The share of results that reach an external system (OpenLineage, catalog or metrics), which measures ecosystem fit

---

## 7. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `datacontract-cli`, Soda or GX add equivalent regulated-finance features | Medium | Stay ODCS-compatible so rule packs and evidence can run *on top of* other engines if needed. The moat is domain content and trust, not the engine. |
| ODCS changes shape | Medium | Pin a supported ODCS version, write conformance tests against the official JSON Schema, and join the Bitol community. |
| Building to a platform bar without user feedback leads to wrong priorities | Medium–High | Adoption doesn't gate the build, but get 2–3 design partners early anyway, and use their feedback to *order* the work inside each phase. Dogfood on the BNM worked example and a synthetic bank dataset. |
| Security of expression evaluation as backends grow | Medium | Replace `df.eval` with our own restricted grammar compiled to parameterised SQL. Fuzz-test it. |
| Maintainer bandwidth: a platform-grade target is ~18 engineer-months or more | High | Strict non-goals (§4). Reuse SQLGlot, DuckDB, SQLAlchemy and the OpenLineage client instead of writing drivers. Automate the bar in CI (benchmarks, conformance, security scans) so quality doesn't depend on manual discipline. Recruit contributors after Phase 1. |
| Regulated users need on-premises, air-gapped deployment | High (in this segment) | Keep the control plane self-hostable: Helm chart, no mandatory SaaS dependency, offline docs. |

---

## 8. Immediate next steps

1. ~~Fix B1–B3 and ship them~~ (done: **v1.4.0**).
2. ~~Fix B4 and B5: immutable registry versions, API tokens on writes, Alembic migrations~~ (done).
3. Write a one-page mapping from `datacontract/v1` to ODCS v3 and decide how extension fields are handled.
4. Build the OpenLineage emitter and an Airflow operator: the smallest change that makes Akad visible to the rest of the platform.
5. Start the MFRS 9 / credit-risk rule pack, using the [BNM worked example](examples.md) and a synthetic financing book as its test data.
6. Talk to data teams at Malaysian banks, takaful operators and fintechs to find 2–3 design partners.
