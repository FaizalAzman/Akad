# Workflow

## Step 1 — Check contract syntax

```bash
akad check --contract contracts/sales.yaml
# OK  daily_sales v1.0.0 — contract is valid
```

## Step 2 — Start the registry

```bash
docker compose up -d
```

- Registry API: `http://localhost:8000`
- Dashboard: `http://localhost:8501`

Writes to the registry need an API token. The compose file sets a development token, `dev-token-change-me`, unless you set `AKAD_API_TOKENS` yourself:

```bash
export AKAD_API_TOKEN=dev-token-change-me   # picked up by the CLI and the SDK
```

## Step 3 — Publish the contract

```bash
akad publish --contract contracts/sales.yaml --registry-url http://localhost:8000
# Published daily_sales v1.0.0
```

## Step 4 — Validate in your pipeline

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

## Step 5 — Use in Airflow

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
