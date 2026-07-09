# Sandbox Job Framework

A lightweight framework for writing Python transformation jobs that publish queryable tables to a shared AWS data sandbox.

---

## What it does

- You write a Python function. The framework runs it on a schedule and puts the results in Athena.
- You can also use the same `sandbox.io` module interactively in Jupyter notebooks.

---

## Quickstart

### 1. Create a job

```bash
uv run sandbox job init
```

You'll be prompted for a name, type, owner, output tables, and description. A job file is created in the right folder automatically.

### 2. Fill in `main()`

Open the generated file and write your logic:

```python
"""Daily summary of orders by customer."""

OWNER = "analytics"
OUTPUT_TABLES = ["orders_by_customer"]


def main() -> None:
    from sandbox import io, run_date

    date = run_date()
    df = io.query(f"""
        SELECT customer_id, COUNT(*) AS order_count, SUM(total) AS revenue
        FROM source_db.orders
        WHERE order_date = DATE '{date}'
        GROUP BY customer_id
    """)
    io.write_table(df, "orders_by_customer")
```

> Use `sandbox.run_date()` to get the logical run date as a `datetime.date` object — don't hardcode dates.

### 3. Open a PR

Push your branch and open a pull request. A GitHub Actions workflow validates your job file automatically. Fix any reported errors before merging.

### 4. It runs

- **Daily jobs** run every morning on a schedule.
- **One-time jobs** run automatically when your PR is merged to `main`.

### 5. Query the results

Once a job has run, its output is a table in Athena:

```sql
SELECT * FROM sandbox_db.orders_by_customer LIMIT 100;
```

---

## Job types

| Type | Location | When it runs |
|---|---|---|
| `daily` | `sandbox/jobs/daily/` | Every day on schedule |
| `one_time` | `sandbox/jobs/one_time/` | Once, when merged to `main` |

**Daily jobs** should be safe to rerun — the default `if_exists="replace"` in `write_table` handles this.

**One-time jobs** run exactly once. If a job fails, it is retried on the next trigger. Once it succeeds, it is permanently skipped. To abandon a broken one-time job permanently, delete the file in a follow-up PR.

---

## Examples

### Daily job

```python
"""Daily active user counts."""

OWNER = "product"
OUTPUT_TABLES = ["daily_active_users"]


def main() -> None:
    from sandbox import io, run_date

    date = run_date()
    df = io.query(f"""
        SELECT COUNT(DISTINCT user_id) AS dau
        FROM source_db.events
        WHERE event_date = DATE '{date}'
    """)
    io.write_table(df, "daily_active_users")
```

### One-time job

```python
"""2026-06-01 backfill of historical order totals."""

OWNER = "data_eng"
OUTPUT_TABLES = ["order_totals_backfill"]


def main() -> None:
    from sandbox import io

    df = io.query("""
        SELECT order_id, SUM(line_total) AS total
        FROM source_db.order_lines
        WHERE order_date < DATE '2026-01-01'
        GROUP BY order_id
    """)
    io.write_table(df, "order_totals_backfill")
```

Name one-time jobs with a date prefix so the filename communicates intent: `2026_06_01_backfill_order_totals.py`.

---

## The `sandbox.io` API

Use these functions inside `main()` or in a Jupyter notebook.

```python
from sandbox import io

# Run a read-only SQL query against Athena and get a DataFrame.
# Only SELECT/WITH/SHOW/DESCRIBE/EXPLAIN statements are accepted —
# use write_table() and delete_table() for mutations.
df = io.query("SELECT * FROM source_db.orders WHERE order_date >= DATE '2026-01-01'")

# Read an entire sandbox table
df = io.read_table("orders_by_customer")

# Write a DataFrame as a sandbox table (replaces existing data by default)
io.write_table(df, "my_table")

# Append to an existing table instead of replacing
io.write_table(df, "my_table", if_exists="append")

# List your sandbox tables
tables = io.list_tables()

# Delete a sandbox table (interactive/notebook use only — blocked during job runs)
io.delete_table("my_table", confirm=True)
```

All functions are safe to `import` in notebooks without any job context. Configuration is read lazily from environment variables at call time.

---

## Notebook usage

In a Jupyter notebook with your AWS credentials configured:

```python
from sandbox import io

# Read source data
orders = io.query("SELECT * FROM source_db.orders LIMIT 1000")

# Explore and transform
summary = orders.groupby("customer_id")["total"].sum().reset_index()

# Write to sandbox for others to query
io.write_table(summary, "customer_spend_explore")

# See what sandbox tables exist
io.list_tables()
```

You need standard AWS credentials in your environment (SSO, `~/.aws/credentials`, or env vars). Set `SANDBOX_BUCKET`, `SANDBOX_DATABASE`, and optionally `SANDBOX_ATHENA_OUTPUT` to point at the shared sandbox.

---

## Table naming rules

- Lowercase letters, digits, and underscores only: `orders_clean`, `dau_2026`
- Must start with a letter: `orders` ✓, `1orders` ✗
- Maximum 128 characters
- No uppercase: `Orders` ✗
- Names starting with `sandbox_` are reserved for the platform

Each table may only be declared by one job. Declaring the same table in two jobs is a validation error.

---

## Run date

`sandbox.run_date()` returns the **logical date** the job is processing as a `datetime.date`.

```python
from sandbox import run_date

date = run_date()  # e.g. datetime.date(2026, 6, 25)
```

- In scheduled runs, it is set to the UTC date of the workflow execution.
- In manual runs, you can override it via the `run_date` input in the GitHub Actions UI.
- In notebooks, it defaults to today (UTC).

Use it instead of `datetime.date.today()` so your job processes the correct date when triggered manually for a past date.

---

## Rerun semantics

| Job type | Ran before? | Behaviour |
|---|---|---|
| `daily` | Yes | Reruns and replaces output (idempotent by default) |
| `one_time` | Succeeded | **Skipped permanently** |
| `one_time` | Failed | Retried on next trigger |

To rerun a one-time job's logic, create a new job file with a new name. Do not rename or edit the old file to bypass the skip — the framework tracks completion by filename.

---

## Querying run history

Every job run appends a record to `sandbox_job_runs` in Athena:

```sql
-- Recent runs
SELECT job_id, status, started_at, duration_seconds
FROM sandbox_job_runs
WHERE run_date >= '2026-06-01'
ORDER BY started_at DESC;

-- Failed jobs this week
SELECT job_id, error_message, started_at
FROM sandbox_job_runs
WHERE status = 'failed'
  AND run_date >= '2026-06-19';

-- Jobs that wrote to a specific table
SELECT job_id, started_at, status
FROM sandbox_job_runs
WHERE table_writes LIKE '%"orders_by_customer"%';
```

---

## Running locally

One-time setup: copy `.env.example` to `.env` and fill in the values. The
`sandbox` CLI loads it automatically — no shell configuration needed. You also
need AWS credentials configured (e.g. via `aws configure` or SSO).

```bash
# Validate all job files — the same check CI runs on your PR
uv run sandbox validate

# Run a single job locally without writing anything
uv run sandbox job run my_job --dry-run

# Run a single job for a specific logical date
uv run sandbox job run my_job --run-date 2026-07-01
```

`sandbox job run` uses the same runner as CI: one-time jobs that already
succeeded are skipped, and successful non-dry runs are recorded.

---

## Manually triggering a run

Go to **Actions → Run Sandbox Jobs → Run workflow** in GitHub and fill in:

| Input | Description |
|---|---|
| `type` | `daily` or `one_time` |
| `job` | Optional: a specific job ID (filename stem) to run |
| `run_date` | Optional: override the logical date (`YYYY-MM-DD`) |
| `dry_run` | If checked, job code runs but no tables are written and no state is updated |

A dry run prints what *would* happen without making any changes — useful for testing before a scheduled run.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SANDBOX_BUCKET` | Yes | S3 bucket name (no `s3://` prefix) |
| `SANDBOX_DATABASE` | Yes | Glue database name for sandbox tables |
| `SANDBOX_ATHENA_OUTPUT` | Recommended | S3 path for Athena query results |
| `SANDBOX_WORKGROUP` | No | Athena workgroup (uses account default if unset) |
| `SANDBOX_RUN_DATE` | No | Override logical run date (`YYYY-MM-DD`) |
| `SANDBOX_DRY_RUN` | No | Set to `true` to skip all mutations |

Locally, put these in a `.env` file at the repo root (copy `.env.example`) — the `sandbox` CLI loads it automatically. Variables already set in your shell take precedence over the file. In GitHub Actions, `SANDBOX_BUCKET`, `SANDBOX_DATABASE`, and `SANDBOX_ATHENA_OUTPUT` are set from repository variables; no `.env` file exists there.

---

## Rules for job authors

1. **Create jobs with `uv run sandbox job init`** — don't hand-create files.
2. **Recurring work goes in `daily/`; run-once work goes in `one_time/`.**
3. **All logic lives inside `main()`** — nothing runs at import time.
4. **Use `io.write_table()` for all sandbox outputs.**
5. **Make daily jobs safe to rerun** — the default `if_exists="replace"` handles this.
6. **Never reuse an old one-time job's filename** — for a new backfill or rerun, create a new file with a new name.
7. **Don't write to a table owned by another job** without coordinating first.
8. **Keep jobs small enough for a GitHub Actions runner** — for large backfills, consider splitting into multiple one-time jobs.
