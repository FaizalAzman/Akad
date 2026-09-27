# Architecture

Akad has three independently-deployable components — the **SDK** (embedded in your pipeline), the **registry** (a small FastAPI service), and the **dashboard** (a read-only view onto the registry). Only the SDK is required; the registry and dashboard are optional but unlock breach history and observability.

```mermaid
flowchart TD
    subgraph Pipeline["Producer Pipeline — Airflow / script / CI"]
        SDK["Akad SDK<br/>DataContractValidator"]
    end

    YAML[("Contract YAML<br/>local file")]
    SDK -- "contract_path=" --> YAML

    subgraph Engine["Validation Engine"]
        Readers["Readers<br/>Parquet · SQL"]
        Validators["Validators<br/>Schema · Freshness · Volume · Quality · Business Rules"]
        Readers --> Validators
    end

    SDK --> Engine

    Notifiers["Notifiers<br/>Webhook · Email"]
    Validators -- "on breach" --> Notifiers

    Registry[("Registry API<br/>FastAPI + SQLAlchemy")]
    SDK -- "contract_name= / publish / post result" --> Registry

    DB[("PostgreSQL / SQLite")]
    Registry --> DB

    Dashboard["Dashboard<br/>FastAPI + Jinja2 + Tailwind"]
    Registry --> Dashboard
    Dashboard --> Consumer["Consumer / Data Engineer"]
```

## Components

### SDK (`akad`)

The only required piece. `DataContractValidator` loads a contract (from a local YAML file or by name from the registry), runs it through the validation engine, optionally dispatches breach notifications, and optionally posts the result back to the registry. This is what you install on Airflow workers — it has no FastAPI/Uvicorn dependency.

### Validation Engine

Orchestrates two pluggable layers:

- **Readers** (`akad.readers`) — load the dataset into a DataFrame. Built-in: `ParquetReader`, `SQLReader`.
- **Validators** (`akad.validators`) — evaluate clauses against the DataFrame. Built-in: schema, freshness, volume, quality, business rules (cross-column/conditional expressions via `df.eval()`). Both are one-method ABCs (`DataReader`, `Validator`) — add a new format or rule by implementing one method and registering it, with no changes to the engine itself.

A validator is never allowed to crash the run: exceptions are caught and converted into `ERROR` clause results, so one buggy custom rule can't take down the whole validation.

### Registry (`registry/`)

A FastAPI service backed by SQLAlchemy (PostgreSQL in production, SQLite for local dev). Stores contract versions and validation history, and serves them over a REST API with interactive docs at `/docs`. The SDK's `RegistryClient` treats posting a validation result as best-effort (a registry outage never crashes a pipeline run), but publishing a contract and fetching one by name as load-bearing: a publish that silently didn't happen, or a missing contract, is a real failure. Writes need a bearer token, published versions are immutable, and the schema is migrated on startup. See [Running the Registry](registry.md).

### Dashboard (`dashboard/`)

A read-only FastAPI + Jinja2 application, styled with Tailwind via CDN (no frontend build step). Renders four views against the registry's REST API: overview (stats + recent breaches), per-contract detail and validation history, breach history with status filters, and contract discovery/search.

### Profiler (`akad.profiler`)

Not a deployed component — a dev-time tool used by `akad infer`. Reads a dataset through the same reader layer the engine uses, then profiles it to scaffold a starter contract: inferred column types, `allowed_values` for low-cardinality string columns, quality rules that mirror the validators' own formulas (so the contract is self-consistent against the data it was generated from), and a volume band around the observed row count. See the [SDK Reference](sdk-reference.md#akadprofiler-programmatic-contract-inference) for the public functions.

### Differ (`akad.differ`)

Also not a deployed component — a pure, I/O-free comparison used by `akad diff`. Takes two already-loaded `DataContract` objects (from files or `RegistryClient.get_contract_version()`) and classifies every change as breaking or non-breaking for a consumer relying on the old contract: removing a column, a business rule, or loosening a numeric bound (a higher `max_rows`, a lower `min_value`, a wider `allowed_values` set) is breaking; tightening one is not. A changed business rule expression is conservatively always breaking — its strictness can't be inferred statically. See the [SDK Reference](sdk-reference.md#akaddiffer-programmatic-breaking-change-detection) for the full rule table.

## Design decisions worth knowing

- **`on_breach: warn` vs `on_breach: fail`** — warn records the breach and lets the pipeline continue (detection); fail raises `DataContractBreachError`, which in Airflow propagates into a failed task and skips downstream tasks (prevention). Both modes post the same `ValidationResult` to the registry.
- **Fail closed** — an `ERROR` result (the dataset couldn't be read, or a rule couldn't be evaluated) is never treated as a pass: under `on_breach: fail` it raises `DataContractEvaluationError`, and `akad validate` exits `2` in either mode.
- **Two contract-loading paths** — `contract_path` for dev/CI where the YAML lives next to the code, `contract_name` + `registry_url` for Airflow workers that shouldn't need a local copy of every contract they validate against.
- **`validate_dataframe()`** — the engine's DataFrame-in entry point bypasses the reader layer entirely, so unit tests can exercise every validator without touching real storage.
