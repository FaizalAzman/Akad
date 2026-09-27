# Python SDK Reference

## `DataContractValidator`

```python
from akad import DataContractValidator, DataContractBreachError

# Option A — from local file
validator = DataContractValidator(
    contract_path="contracts/sales.yaml",
    registry_url="http://localhost:8000",   # optional — enables breach history
    registry_token=None,                    # optional — defaults to $AKAD_API_TOKEN
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

### Exceptions (`on_breach: fail` only)

| Exception | Raised when |
|---|---|
| `DataContractBreachError` | The dataset was evaluated and violated the contract (`BREACH`) |
| `DataContractEvaluationError` | The contract couldn't be evaluated (`ERROR`): unreadable dataset, erroring rule or validator |
| `DataContractError` | Base class of both; catch it to handle either. `exc.result` holds the full `ValidationResult` |

```python
from akad import DataContractError

try:
    validator.validate()
except DataContractError as exc:
    print(exc.result.overall_status, exc.result.error_message)
    raise
```

Under `on_breach: warn` neither is raised; check `result.overall_status` yourself.

## `ValidationResult`

```python
result.overall_status      # OverallStatus.COMPLIANT | BREACH | ERROR
result.is_breach           # bool
result.row_count           # int
result.failed_clauses      # List[ClauseResult] — status FAIL
result.errored_clauses     # List[ClauseResult] — status ERROR (a rule that couldn't be evaluated)
result.error_message       # str | None — set when the dataset couldn't be read

for c in result.failed_clauses:
    print(c.clause_type)   # e.g. "schema.allowed_values"
    print(c.clause_target) # column name
    print(c.message)       # human-readable explanation
```

## `validate_dataframe()` — for unit testing

```python
from akad.engine import validate_dataframe
import pandas as pd

df = pd.DataFrame({"sale_id": ["A", "B"], "amount": [10.0, 20.0]})
result = validate_dataframe(df, contract)
```

## Custom validator plugin

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

## `akad.profiler` — programmatic contract inference

The same logic behind [`akad infer`](cli-reference.md#akad-infer-scaffold-a-starter-contract) is available as a library, if you'd rather generate a starter contract from code than the CLI:

```python
import pyarrow.parquet as pq
from akad.profiler import generate_contract, contract_to_yaml_dict

df = pq.read_table("data/daily_sales.parquet").to_pandas()

contract = generate_contract(
    df,
    name="daily_sales",
    dataset_format="parquet",
    owner_team="Data Engineering",
    owner_email="data@example.com",
    location="data/daily_sales.parquet",
)

print(contract_to_yaml_dict(contract))   # dict, ready for yaml.dump()
```

`generate_contract()` returns a fully-validated `DataContract` — same model the rest of the SDK uses — so it can be passed directly to `validate_dataframe()` without a round-trip through YAML. As with the CLI command, treat the result as a starting point: review `allowed_values` and volume bounds before relying on it.

## `akad.differ` — programmatic breaking-change detection

The same logic behind [`akad diff`](cli-reference.md#akad-diff-flag-breaking-changes-before-you-publish) is available as a library — useful for a pre-merge check that needs to do more than print to a terminal:

```python
from akad.contract_loader import load_contract
from akad.differ import DiffSeverity, diff_contracts

old = load_contract("contracts/daily_sales.yaml")
new = load_contract("contracts/daily_sales.next.yaml")

for entry in diff_contracts(old, new):
    print(entry.severity, entry.path, entry.message)

breaking = [e for e in diff_contracts(old, new) if e.severity == DiffSeverity.BREAKING]
if breaking:
    raise SystemExit(f"{len(breaking)} breaking change(s) — see above")
```

`diff_contracts()` is a pure function — no I/O, no registry access — so it works equally well on two contracts loaded from files, fetched from the registry via `RegistryClient.get_contract_version(name, version)`, or built in memory (e.g. in a test asserting a proposed change is non-breaking).

For auto-generated signatures and docstrings, see the [API Reference](api-reference.md).
