# Product Review & Roadmap

*Status: proposal · Last reviewed: 2026-09-27 · Baseline: akad-framework v1.3.0*

This document answers three questions:

1. **Is Akad worth investing in as a product, or should it stay a personal tool?**
2. **If we build it, what does it have to become to live inside a real data platform** (orchestrators, catalogs, lineage, warehouses, lakehouses), rather than standing alone?
3. **In what order, and how do we know when to stop?**

---

## 1. Verdict

**Build, but not as a general-purpose data contract tool. Build it as the interoperable contract layer for regulated financial data, starting with Islamic finance and ASEAN regulatory reporting.**

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

### If we don't go ahead

If the go/no-go gates in §6 aren't met, the fallback is still worthwhile: ship Phase 0 and Phase 1 only. That leaves Akad as a small, correct, ODCS-compatible validator that people can drop into Airflow and Dagster, and ends active product investment there.

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

### Phase 0: Credibility (≈ 2–3 weeks) · *"Correct before clever"*
Goal: nothing in the current product gives a wrong answer.

- [ ] **B1**: Treat `ERROR` as a failure: SDK raises under `on_breach: fail`, CLI exits non-zero (a distinct code, e.g. `2`), plus an explicit `on_error: fail|warn` setting that defaults to `fail`.
- [ ] **B2**: A type system that understands Arrow: check against the Arrow schema before converting to pandas. Distinguish `date` from `timestamp`, support `decimal(p,s)`, and handle timezones.
- [ ] **B3**: `publish_contract` raises on failure. The CLI exits non-zero and prints the registry's error.
- [ ] **B4**: Unique `(name, version)`, `409 Conflict` on republish, atomic "current" flip, semver validation.
- [ ] **B5**: Token-based auth on registry writes (API keys), read-only by default for anonymous users.
- [ ] Alembic migrations in place of `create_all`.
- [ ] Lower the core Python floor to 3.10 (keep 3.12 for the registry and dashboard if needed).
- [ ] Remove or implement the dead model fields (`catalog_*`, `consumers`) and document what exists.

**Exit criteria:** every bug above has a regression test, and a test suite runs a real Parquet file with decimal, date and timestamp columns end to end.

### Phase 1: Interoperability (≈ 6–8 weeks) · *"Speak the ecosystem's language"*
Goal: a team already using Airflow, Dagster, dbt or a catalog can adopt Akad without changing how they work.

- [ ] **ODCS v3 as the native format.** Read and write ODCS, keep `datacontract/v1` as a deprecated input with `akad migrate`, and put Akad-specific rules under `x-akad` or ODCS `quality` custom rules.
- [ ] **`akad diff` on ODCS**, and as a GitHub Action that comments on PRs with breaking changes.
- [ ] **OpenLineage emitter.** Each validation emits a run event with a `DataQualityAssertions` facet.
- [ ] **Airflow provider package** (`apache-airflow-providers-akad` style): operator, deferrable sensor, connection type, lineage integration.
- [ ] **Dagster integration**: contract → asset checks factory.
- [ ] **dbt import**: `akad import dbt --manifest target/manifest.json`.
- [ ] **Structured results schema** (JSON Schema published, versioned) so downstream tools can depend on it.
- [ ] Row-level evidence: each failed clause carries the violating row count, a capped sample of primary keys, and the SQL or expression that found them.

**Exit criteria:** the same contract runs from the CLI, Airflow and Dagster, and its results appear in Marquez or DataHub with no custom glue.

### Phase 2: Scale (≈ 8–10 weeks) · *"Move the check, not the data"*
Goal: validate a 1 TB warehouse table or a partitioned Iceberg table in seconds to minutes, with bounded memory.

- [ ] **A rule compiler behind a backend interface**: contract clauses → an IR → SQL (via SQLGlot) / DuckDB / PySpark / pandas. Aggregate checks (null %, duplicates, min/max, row count, freshness) become a single scan query.
- [ ] **Business rules compiled to SQL** as `COUNT(*) WHERE NOT (<expr>)`, using a safe, restricted expression grammar that is shared across backends instead of relying on `df.eval` semantics.
- [ ] Connectors: Snowflake, BigQuery, Databricks/Unity, Postgres, Trino; Iceberg and Delta via DuckDB; object storage with native last-modified for freshness.
- [ ] **Partition- and increment-scoped validation** (only the newly landed data), with an option for whole-table checks on a slower cadence.
- [ ] **Quarantine / write-audit-publish support**: write failing rows to a quarantine table, or validate an Iceberg branch or Delta version before promoting it.
- [ ] Metrics: Prometheus endpoint and OTel traces. Results sink table.
- [ ] Performance benchmarks published in the docs (rows per second, memory ceiling).

**Exit criteria:** validating a 1B-row table uses constant client memory, and every built-in rule produces identical results on all backends (a cross-backend conformance test suite).

### Phase 3: Governance control plane (≈ 10–12 weeks) · *"Contracts as agreements, not just tests"*
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

**Exit criteria:** at least one design-partner institution uses a rule pack and an evidence export in an actual audit or regulatory cycle.

### Illustrative timeline

```
2026 Q4        2027 Q1              2027 Q2               2027 Q3              2027 Q4 →
├─ Phase 0 ─┤
            ├──── Phase 1 ────┤
                              ├───── Phase 2 ─────┤
                                                  ├────── Phase 3 ──────┤
                         ├──────────── Phase 4 (design partners, rule packs; runs in parallel) ────────→
```

---

## 6. Go/no-go gates and success metrics

The biggest risk is building a platform nobody adopts, so each phase has a gate. **Line up design partners before Phase 2.** Phase 2 and later are expensive, and should only be built with real users pulling for them.

| Gate | Pass condition | If it fails |
|---|---|---|
| After Phase 0 | All B1–B5 fixed. Release 1.4 published. | n/a (mandatory) |
| After Phase 1 | ≥ 3 external teams run Akad in an orchestrator. ≥ 1 regulated-finance design partner signed up. Visible community pull (issues, PRs, stars trend). | Stop at Phase 1: maintain as an ODCS-compatible library and stop the platform investment. |
| After Phase 2 | A design partner validates production-scale tables through pushdown, and cross-backend conformance is green. | Narrow the backends to what partners actually use (e.g. Postgres and Databricks only). |
| After Phase 3 | ≥ 1 partner uses the breaking-change gate and consumer subscriptions in their change process. | Keep the registry thin and push governance to catalogs (DataHub or OpenMetadata) instead. |

**North-star metric:** *contract-protected pipeline runs per week*, meaning runs where an Akad check executed and its result was recorded.

**Supporting metrics:**
- Time to first validated contract (target: < 15 min with `akad infer`)
- Breaches caught before a downstream consumer noticed (user-reported)
- Breaking changes blocked at publish
- The share of results that reach an external system (OpenLineage, catalog or metrics). This measures ecosystem fit, not standalone use.

---

## 7. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `datacontract-cli`, Soda or GX add equivalent regulated-finance features | Medium | Stay ODCS-compatible so rule packs and evidence can run *on top of* other engines if needed. The moat is domain content and trust, not the engine. |
| ODCS changes shape | Medium | Pin a supported ODCS version, write conformance tests against the official JSON Schema, and join the Bitol community. |
| Too few design partners in a narrow vertical | Medium–High | Phase 1 is useful horizontally anyway. The Phase 1 gate caps the downside. |
| Security of expression evaluation as backends grow | Medium | Replace `df.eval` with our own restricted grammar compiled to parameterised SQL. Fuzz-test it. |
| Maintainer bandwidth (single maintainer) | High | Strict non-goals (§4). Adopt existing connectors (SQLGlot, DuckDB, SQLAlchemy) rather than writing drivers. |
| Regulated users need on-premises, air-gapped deployment | High (in this segment) | Keep the control plane self-hostable: Helm chart, no mandatory SaaS dependency, offline docs. |

---

## 8. Immediate next steps

1. Fix B1–B3 (small, high-impact) and ship **v1.3.1**.
2. Write a one-page ODCS mapping from `datacontract/v1` to ODCS v3 and decide how extension fields are handled.
3. Build an OpenLineage emitter and an Airflow operator spike: the smallest change that makes Akad visible to the rest of the platform.
4. Talk to 5–8 data teams at Malaysian banks, takaful operators and fintechs. Validate the regulated-finance positioning before starting Phase 2.
