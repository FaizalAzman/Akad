# Contract YAML Reference

!!! warning "Unknown keys are ignored, with a warning"
    A key Akad doesn't recognise, usually a typo such as `freshnes:` or `max_null_percentag:`, is ignored, so the rule it was meant to declare is **not enforced**. Akad warns whenever a contract with unknown keys is loaded, and `akad check --strict` fails on them. Run it in CI. Unknown keys will become an error in the next major version.

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
