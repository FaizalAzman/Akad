# Akad

[![PyPI version](https://img.shields.io/pypi/v/akad-framework.svg?cacheSeconds=300)](https://pypi.org/project/akad-framework/)
[![Python versions](https://img.shields.io/pypi/pyversions/akad-framework.svg?cacheSeconds=300)](https://pypi.org/project/akad-framework/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-mkdocs--material-blue.svg)](https://parmenidessartre.github.io/Akad/)

**Akad** (Malay/Arabic: *contract, covenant* — the term for the underlying contract of any Islamic finance product) is a lightweight Python library for defining, enforcing, and monitoring data quality contracts on batch datasets. Built for data engineering pipelines — works standalone, in Airflow, or any Python environment.

```bash
pip install akad-framework
```

## Table of Contents

- [What it does](#what-it-does)
- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Workflow](#workflow)
- [Contract YAML Reference](#contract-yaml-reference)
- [CLI Reference](#cli-reference)
- [Python SDK Reference](#python-sdk-reference)
- [Development Setup](#development-setup)
- [Contributing](#contributing)
- [License](#license)

---

## What it does

When a producer pipeline changes a dataset (renames a column, drops rows, adds bad values), downstream consumers break silently. Akad gives you:

- A **contract file** (YAML) that declares what the dataset must look like
- An **enforcement engine** that validates the dataset against the contract at pipeline runtime
- A **registry** that stores contract versions and validation history
- A **CLI** for manual validation and contract management
- A **dashboard** to monitor all contracts across your data platform

---

## Features

### Validation Rules

| Feature | What it checks |
|---|---|
| **Schema — column existence** | Every declared column is present in the dataset |
| **Schema — column types** | Column dtype matches declared type (`string`, `integer`, `float`, `boolean`, `date`, `timestamp`) |
| **Schema — nullable** | Non-nullable columns have zero null values |
| **Schema — allowed values** | Column contains only the declared set of allowed values |
| **Schema — no extra columns** | Dataset has no undeclared columns (optional, off by default) |
| **Freshness** | Dataset was updated within `max_age_hours`; uses file mtime or `max(check_column)` |
| **Volume** | Row count is within `min_rows` / `max_rows` bounds |
| **Quality — null rate** | Column null percentage does not exceed `max_null_percentage` |
| **Quality — duplicate rate** | Column duplicate percentage does not exceed `max_duplicate_percentage` |
| **Quality — value range** | Column values are within `min_value` / `max_value` bounds |
| **Business rules** | Cross-column/conditional expressions hold for every row (e.g. `status != 'COMPLETED' or ship_date.notnull()`) |

### Dataset Formats

| Format | How |
|---|---|
| **Parquet** | Local path or S3 via `pyarrow` |
| **SQL** | Any SQLAlchemy-supported database (PostgreSQL, MySQL, SQLite) via `table_name` + `connection_string` |

### Business Rules

Cross-column and conditional checks that the column-level Schema/Quality rules can't express — backed by [pandas' own expression evaluator](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.eval.html) (`df.eval(..., engine="python")`), not Python's `eval()`. It has no access to builtins, imports, or arbitrary function calls — only column references, comparisons, boolean logic, and a handful of pandas methods like `.isnull()`.

```yaml
business_rules:
  - name: ship_date_required_when_completed
    expression: "status != 'COMPLETED' or ship_date.notnull()"
    description: "Completed orders must have a ship date"
  - name: end_after_start
    expression: "end_date >= start_date"
```

A rule fails if *any* row violates it; the failure message reports how many rows did. A malformed expression becomes an `ERROR` clause, not a crash.

### Breach Modes

| Mode | Behaviour |
|---|---|
| `on_breach: warn` | Returns result with `is_breach=True`, pipeline continues |
| `on_breach: fail` | Raises `DataContractBreachError`, pipeline halts |

If the contract can't be evaluated at all (unreadable dataset, erroring rule) the result is `ERROR`. Under `on_breach: fail` that raises `DataContractEvaluationError`, so a gate that can't see the data never lets the pipeline through. Both exceptions subclass `DataContractError` and carry the full result as `.result`.

### Contract Loading

| Method | When to use |
|---|---|
| `contract_path="contracts/sales.yaml"` | Dev machine, CI — file is local |
| `contract_name="daily_sales"` + `registry_url=...` | Airflow workers, remote runners — no local file needed |

### Notifications

- **Webhook** — POST JSON breach payload to any URL (Slack, Teams, PagerDuty)
- **Email** — SMTP with configurable recipients; password stored in env var, never in YAML

### Registry

- REST API (FastAPI) — publish contracts, fetch by name, list versions, store validation results
- PostgreSQL backend for production; SQLite for local dev
- Interactive API docs at `/docs`

### Observability Dashboard

- FastAPI + Jinja2 + Tailwind (CDN, no build step) — overview of all contracts, compliant vs breach counts, per-contract validation history, breach history with status filters, contract discovery/search

### CLI

- `akad infer` — profile an existing dataset and scaffold a starter contract YAML
- `akad diff` — compare two contract versions, flag breaking vs non-breaking changes (CI-friendly)
- `akad check` — parse and validate YAML syntax without touching data (CI-safe)
- `akad publish` — register a contract version
- `akad validate` — run full validation; exit 1 on breach, 2 if it couldn't be evaluated (CI-friendly)
- `akad list` — list all current contracts in registry
- `akad history` — show recent validation runs for a contract

### Developer Experience

- `validate_dataframe(df, contract)` — skip storage reads in unit tests, pass a DataFrame directly
- Injectable `_http_client` and `_registry_client` — test the full SDK without a real server
- Custom validator plugin API — for logic too complex for a `business_rules` expression (multi-table joins, external API calls, ML-based checks)
- Split dependencies — `pip install akad-framework` (core only) keeps Airflow worker environments lean

---

## Installation

```bash
pip install akad-framework                   # core — Airflow workers, pipelines
pip install "akad-framework[registry]"       # + registry server
pip install "akad-framework[all]"            # everything
```

---

## Quick Start

**1. Write a contract**

```yaml
# contracts/sales.yaml
apiVersion: datacontract/v1
kind: DataContract
metadata:
  name: daily_sales
  version: "1.0.0"
  owner:
    team: Data Engineering
    email: data@example.com
dataset:
  format: parquet
  location: /data/sales/daily.parquet
on_breach: warn
schema:
  columns:
    - name: sale_id
      type: string
      nullable: false
    - name: amount
      type: float
      nullable: false
    - name: currency_code
      type: string
      allowed_values: [MYR, USD, SGD]
volume:
  min_rows: 1000
quality:
  - column: sale_id
    max_null_percentage: 0.0
    max_duplicate_percentage: 0.0
```

**2. Validate**

```bash
akad validate --contract contracts/sales.yaml
# ✓ daily_sales v1.0.0: COMPLIANT

# On breach:
# ✗ daily_sales v1.0.0: BREACH
# Failed clauses:
#   - [schema.allowed_values] [currency_code] Contains values not in allowed list: ['JPY']
```

---

## Workflow

### Step 1 — Check contract syntax

```bash
akad check --contract contracts/sales.yaml
# OK  daily_sales v1.0.0 — contract is valid
```

### Step 2 — Start the registry

```bash
docker compose up -d
```

- Registry API: `http://localhost:8000`
- Dashboard: `http://localhost:8501`

### Step 3 — Publish the contract

```bash
akad publish --contract contracts/sales.yaml --registry-url http://localhost:8000
# Published daily_sales v1.0.0
```

### Step 4 — Validate in your pipeline

**From a local file (dev / CI):**

```python
from akad import DataContractValidator, DataContractBreachError

result = DataContractValidator(
    contract_path="contracts/sales.yaml",
    registry_url="http://localhost:8000",
).validate()

print(result.overall_status)    # COMPLIANT or BREACH
print(result.row_count)         # 48203
```

**From the registry by name (Airflow workers — no local file needed):**

```python
result = DataContractValidator(
    contract_name="daily_sales",
    registry_url="http://akad-registry:8000",
).validate()
```

### Step 5 — Use in Airflow

```python
from airflow.sdk import dag, task
from akad import DataContractValidator
import os

REGISTRY_URL = os.environ.get("AKAD_REGISTRY_URL", "http://akad-registry:8000")

@dag(schedule="@daily", ...)
def sales_pipeline():

    @task
    def extract_and_load() -> int:
        # write dataset to /data/sales/daily.parquet
        ...

    @task
    def validate(row_count: int) -> str:
        result = DataContractValidator(
            contract_name="daily_sales",   # fetched from registry — no local file
            registry_url=REGISTRY_URL,
            notifiers=[],
        ).validate()

        if result.is_breach:
            raise ValueError(f"Contract breach — pipeline halted")

        return result.overall_status.value

    @task
    def transform(status: str) -> None:
        ...  # only runs when validation passes

    rows = extract_and_load()
    status = validate(rows)
    transform(status)
```

On breach: `validate` raises → Airflow marks it FAILED → `transform` is skipped — bad data never reaches downstream consumers.

---

## Contract YAML Reference

```yaml
apiVersion: datacontract/v1
kind: DataContract

metadata:
  name: daily_sales          # unique identifier
  version: "1.0.0"           # semantic version
  owner:
    team: Data Engineering
    email: data@example.com
  tags: [finance, daily]

dataset:
  format: parquet             # parquet | sql
  location: /data/sales/daily.parquet

  # SQL datasets:
  # format: sql
  # connection_string: postgresql://user:pass@host:5432/db
  # table_name: daily_sales

on_breach: warn               # warn | fail

schema:
  enforce_no_extra_columns: false
  columns:
    - name: sale_id
      type: string            # string | integer | float | boolean | date | timestamp
      nullable: false
      allowed_values: [SALE, REFUND]

freshness:
  max_age_hours: 25
  check_column: sale_date     # optional — uses max(column) instead of file mtime

volume:
  min_rows: 1000
  max_rows: 10000000

quality:
  - column: sale_id
    max_null_percentage: 0.0
    max_duplicate_percentage: 0.0
  - column: amount
    min_value: 0.01
    max_value: 9999999.0

business_rules:
  - name: ship_date_required_when_completed
    expression: "status != 'COMPLETED' or ship_date.notnull()"
    description: "Completed orders must have a ship date"   # optional
  - name: end_after_start
    expression: "end_date >= start_date"

notifications:
  webhook:
    url: https://hooks.slack.com/services/YOUR/WEBHOOK/URL
  email:
    smtp_host: smtp.example.com
    smtp_port: 587
    smtp_user: alerts@example.com
    smtp_password_env: SMTP_PASSWORD
    recipients:
      - data-team@example.com
```

---

## CLI Reference

```
akad infer     --name NAME      [--format parquet|sql]  [--location PATH | --connection-string URL --table-name NAME]  [--output PATH]
akad diff      --old PATH --new PATH | --name NAME --old-version V --new-version V --registry-url URL  [--output text|json]
akad check     --contract PATH
akad publish   --contract PATH  --registry-url URL
akad validate  --contract PATH  [--registry-url URL]  [--output text|json]
akad list      --registry-url URL
akad history   --name NAME      --registry-url URL     [--limit N]
```

### `akad infer` — scaffold a starter contract

Profiles an existing dataset and writes a starter contract YAML — column types, nullability, low-cardinality `allowed_values`, key-like column quality rules, and a volume band around the observed row count.

```bash
akad infer --name daily_sales --location data/daily_sales.parquet \
  --owner-team "Data Engineering" --owner-email data@example.com \
  --output contracts/daily_sales.yaml
```

This is a **starting point, not a finished contract** — every inferred rule reflects only what the data looked like when profiled, not the rules it's actually supposed to follow. Review and tighten it (especially `allowed_values` and volume bounds) before relying on it in CI or production.

### `akad diff` — flag breaking changes before you publish

Compares two contract versions and classifies every change as **breaking** or **non-breaking** for a consumer relying on the old contract's guarantees — the rule throughout is that *loosening* a guarantee (removing a column, allowing a new enum value, widening a quality bound) is breaking, while *tightening* one is not.

```bash
# Two local files — e.g. in a pre-merge CI check
akad diff --old contracts/daily_sales.yaml --new contracts/daily_sales.next.yaml

# Two versions already published to the registry
akad diff --name daily_sales --old-version 1.0.0 --new-version 1.1.0 --registry-url http://localhost:8000
```

```
  ✗ BREAKING      schema.columns.region: column removed
  ✗ BREAKING      schema.columns.currency_code.allowed_values: now allows additional values: ['JPY']
  ✓ NON_BREAKING  volume.min_rows: changed from 500 to 1000

2 breaking, 1 non-breaking change(s).
```

Exits `1` if any breaking change is found — drop it into CI to catch contract changes that would break a downstream consumer before they're published.

---

## Python SDK Reference

### `DataContractValidator`

```python
from akad import DataContractValidator, DataContractBreachError

# Option A — from local file
validator = DataContractValidator(
    contract_path="contracts/sales.yaml",
    registry_url="http://localhost:8000",   # optional — enables breach history
    extra_validators=[MyValidator()],       # optional plugins
    notifiers=[],                           # [] disables notifications
)

# Option B — from registry by name (Airflow / remote workers)
validator = DataContractValidator(
    contract_name="daily_sales",
    registry_url="http://localhost:8000",
)

result = validator.validate()
```

### `ValidationResult`

```python
result.overall_status      # OverallStatus.COMPLIANT | BREACH | ERROR
result.is_breach           # bool
result.row_count           # int
result.failed_clauses      # List[ClauseResult]

for c in result.failed_clauses:
    print(c.clause_type)   # e.g. "schema.allowed_values"
    print(c.clause_target) # column name
    print(c.message)       # human-readable explanation
```

### `validate_dataframe()` — for unit testing

```python
from akad.engine import validate_dataframe
import pandas as pd

df = pd.DataFrame({"sale_id": ["A", "B"], "amount": [10.0, 20.0]})
result = validate_dataframe(df, contract)
```

### Custom validator plugin

```python
from akad.validators.base import Validator
from akad.models.result import ClauseResult, ClauseStatus

class MyValidator(Validator):
    def validate(self, df, contract, reader_last_modified):
        ok = df["amount"].sum() > 0
        return [ClauseResult(
            clause_type="custom.positive_total",
            clause_target="amount",
            status=ClauseStatus.PASS if ok else ClauseStatus.FAIL,
            expected="> 0",
            observed=str(df["amount"].sum()),
            message="" if ok else "Total amount must be positive",
        )]

DataContractValidator(
    contract_path="contracts/sales.yaml",
    extra_validators=[MyValidator()],
).validate()
```

---

## Development Setup

```bash
git clone https://github.com/ParmenidesSartre/Akad.git
cd Akad

# Install with all extras + dev tools
pip install uv
uv sync

# Run tests
uv run pytest

# Unit tests only (no Docker)
uv run pytest tests/unit/ -v

# Integration tests (SQLite, no Docker)
uv run pytest tests/integration/ -v

# Lint and type-check (same checks CI runs on every push)
uv sync --group lint
uv run ruff check .
uv run mypy

# Start registry locally
uv run uvicorn registry.main:app --reload --port 8000

# Start dashboard
uv run uvicorn dashboard.main:app --reload --port 8501

# Start everything (registry + dashboard + postgres)
docker compose up -d
```

### Project structure

```
akad/
├── akad/                   # Core package — install this on Airflow workers
│   ├── models/             # Contract and result Pydantic models
│   ├── readers/             # ParquetReader, SQLReader
│   ├── validators/          # Schema, Freshness, Volume, Quality validators
│   ├── notifiers/           # Webhook, Email notifiers
│   ├── engine.py            # Orchestrates readers + validators
│   ├── sdk.py               # DataContractValidator — main public API
│   ├── profiler.py          # akad infer — dataset profiling, starter contract generation
│   ├── cli.py                # akad CLI
│   └── registry_client.py   # HTTP client for the registry
├── registry/               # FastAPI registry service
├── dashboard/              # FastAPI + Jinja2 + Tailwind observability dashboard
├── lab/                    # End-to-end Docker test lab
├── tests/
│   ├── unit/               # Validator unit tests
│   ├── integration/        # Engine + registry API tests
│   └── fixtures/           # Sample contract YAML files
├── contracts/              # Example contracts
├── .github/workflows/      # CI (test + lint), docs deploy, PyPI publish
├── docker-compose.yml
└── pyproject.toml
```

---

## Contributing

Issues and pull requests are welcome. Before submitting a change:

```bash
uv run pytest                # full suite must pass
uv run ruff check .          # lint
uv run mypy                  # type-check
```

All three run automatically on every push/PR via CI — a PR won't merge cleanly if any of them fail. Please keep new functionality covered by tests — the project maintains ~99% coverage.

See [CHANGELOG.md](CHANGELOG.md) for release history.

---

## License

[MIT](LICENSE) © Faizal Azman

---

*akad-framework v1.4.0*
