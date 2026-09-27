# CLI Reference

```
akad infer     --name NAME      [--format parquet|sql]  [--location PATH | --connection-string URL --table-name NAME]  [--output PATH]
akad diff      --old PATH --new PATH | --name NAME --old-version V --new-version V --registry-url URL  [--output text|json]
akad check     --contract PATH
akad publish   --contract PATH  --registry-url URL
akad validate  --contract PATH  [--registry-url URL]  [--output text|json]
akad list      --registry-url URL
akad history   --name NAME      --registry-url URL     [--limit N]
```

| Command | Purpose | CI-friendly |
|---|---|---|
| `akad infer` | Profile an existing dataset and scaffold a starter contract YAML | — |
| `akad diff` | Compare two contract versions; flag breaking vs non-breaking changes | Yes — fail the build on a breaking contract change |
| `akad check` | Parse and validate contract YAML syntax without touching data | Yes — catches typos before they hit a pipeline |
| `akad publish` | Register a contract version with the registry | — |
| `akad validate` | Run full validation against the dataset; exits `0` compliant, `1` breach, `2` could not be evaluated (unreadable dataset, erroring rule, bad contract) | Yes — fail the build on a breach or error |
| `akad list` | List all current contracts in the registry | — |
| `akad history` | Show recent validation runs for a contract | — |

## `akad infer` — scaffold a starter contract

Profiles an existing dataset and writes a starter contract YAML — column types, nullability, low-cardinality `allowed_values`, key-like column quality rules, and a volume band around the observed row count.

```bash
akad infer --name daily_sales --location data/daily_sales.parquet \
  --owner-team "Data Engineering" --owner-email data@example.com \
  --output contracts/daily_sales.yaml
```

For a SQL dataset, use `--format sql --connection-string ... --table-name ...` instead of `--location`.

This is a **starting point, not a finished contract** — every inferred rule reflects only what the data looked like when profiled, not the rules it's actually supposed to follow:

- `allowed_values` is only inferred for string columns where values repeat and stay under a cardinality cap — but it still only knows about values seen in the sample. A rare-but-valid value not present when you ran `infer` will show up as a breach later.
- Volume bounds are a 0.5×–2× band around the observed row count — adjust to your pipeline's actual expected range.
- `on_breach` always defaults to `warn` — switch to `fail` deliberately once you trust the contract.
- Freshness rules are never inferred — there's no reliable signal for `max_age_hours` from a single snapshot.

Review and tighten the output before relying on it in CI or production.

## `akad diff` — flag breaking changes before you publish

Compares two contract versions and classifies every change as **breaking** or **non-breaking** for a consumer relying on the old contract's guarantees.

```bash
# Two local files
akad diff --old contracts/daily_sales.yaml --new contracts/daily_sales.next.yaml

# Two versions already published to the registry
akad diff --name daily_sales --old-version 1.0.0 --new-version 1.1.0 --registry-url http://localhost:8000
```

Exits `1` if any breaking change is found — wire it into CI on the contracts repo to catch breaking changes before they're published, not after a consumer's pipeline breaks.

The rule applied throughout: **loosening** a guarantee is breaking, **tightening** one is not.

| Change | Breaking? | Why |
|---|---|---|
| Column removed | Breaking | A consumer reading that column fails |
| Column added | Non-breaking | Additive — nothing existing depends on it |
| Column type changed | Breaking | Consumer parsing/casting logic may fail |
| `nullable: false → true` | Breaking | Consumer assuming non-null may fail on a null |
| `nullable: true → false` | Non-breaking | Stronger guarantee, strictly compatible |
| `allowed_values` gains a value | Breaking | Exhaustive consumer handling (switch/case) may not cover it |
| `allowed_values` loses a value only | Non-breaking | Strictly fewer cases than before |
| `min_rows` decreased, or removed | Breaking | Weaker lower bound — consumer expecting at least N rows may not get them |
| `max_rows` increased, or removed | Breaking | Weaker upper bound — consumer with a fixed-size assumption may break |
| `max_age_hours` increased, or removed | Breaking | Data may now be staler than a consumer expects |
| `max_null_percentage` / `max_duplicate_percentage` increased, or removed | Breaking | Consumer assuming a stricter cap may break |
| `min_value` decreased, or removed | Breaking | Consumer assuming a stricter floor may break |
| `max_value` increased, or removed | Breaking | Consumer assuming a stricter ceiling may break |
| A quality rule removed entirely | Breaking | A guarantee is gone |
| A quality rule added | Non-breaking | A new guarantee, doesn't affect existing consumers |
| A business rule removed entirely | Breaking | A guarantee is gone |
| A business rule added | Non-breaking | A new guarantee, doesn't affect existing consumers |
| A business rule's expression changed | Breaking (always) | Strictness can't be inferred statically from arbitrary code — flagged conservatively for human review |

Out of scope by design: metadata (name, owner, tags), notifications, and consumer lists aren't compared — they don't affect what the data looks like to a consumer. `on_breach` and `check_column` changes are pipeline-internal, not surfaced either.
