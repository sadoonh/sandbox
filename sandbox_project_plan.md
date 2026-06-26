# Sandbox Job Framework — Project Plan 

## 1. Project Purpose

Build a lightweight internal framework that lets users write Python transformation jobs and publish the results into a shared AWS-based sandbox.

Users should be able to:

- Create a new sandbox job from the command line with `uv run sandbox job init`.
- Write Python logic that reads cataloged source data and transforms it with pandas.
- Write results to the sandbox as queryable tables with one function call.
- Run recurring jobs on a daily schedule.
- Run one-time jobs exactly once after they are merged.
- See what ran, when, and whether it succeeded.
- **Use the same `sandbox.io` module interactively in Jupyter notebooks** to read, write, list, and delete sandbox tables against the data lake.

This is NOT an orchestration platform. The target users know basic Python and pandas. Every design decision should favor "less for the user to learn" over framework features.

---

## 2. Core Workflow

```text
User runs `uv run sandbox job init`  →  job file created from template
User fills in main()                 →  opens a PR
Validation workflow checks the job  →  PR merged
GitHub Actions runs the job         →  output table queryable in Athena
One JSON log record written to S3   →  user can see what happened
```

Notebook workflow (no jobs involved):

```python
from sandbox import io
df = io.query("SELECT * FROM source_db.orders WHERE ...")
io.write_table(df_clean, "orders_clean")
```

---

## 3. Key Design Decisions

1. **awswrangler** is the AWS layer. Do not hand-roll S3/Glue/Athena plumbing with boto3/pyarrow. `io.py` should mostly be thin wrappers around `awswrangler` calls plus table-name validation and optional logging.
2. **Jobs define `main()` with no arguments.** There is no `ctx` object. Runtime values come from small helper functions (see §7).
3. **Job ID and job type are derived, never declared.** Job ID = filename stem. Job type = parent folder (`daily/` or `one_time/`). No `JOB_ID` or `JOB_TYPE` constants exist, so they can never disagree with the file's location.
4. **The interactive CLI stays.** `uv run sandbox job init` is the official way to create a job. The user experience is: "run `sandbox job init` in your terminal and add code to the file it creates."
5. **The full io API stays** (5 functions, §6) because notebook users need it standalone.
6. **One log record per job run** (not separate job-run and table-write log streams). Table write details are embedded in the run record.
7. **Logs are a queryable table, not loose files.** The runner appends log records to a Parquet dataset in S3 registered as a Glue table (`sandbox_job_runs`), partitioned by run date. Users query their job history with plain SQL in Athena. No per-run JSON files accumulating in a prefix.
8. **One-time job state is a single JSON file in S3** (`completed_jobs.json`), not a directory of marker files and not derived from the log table. State is read-modify-write and must be bulletproof; deriving "already ran" from log queries would make run/skip correctness depend on Athena availability and partition registration timing. Keep state dumb and separate from observability. Treat this file as platform state, not a public framework API; the framework does not expose edit commands for it, and write protection depends on IAM (ideally only GitHub Actions/admin roles can write `sandbox-platform/state/*`).
9. **Two GitHub Actions workflows** (validate + run), not three.
10. **One-time jobs auto-run on merge**, and only jobs not present in `completed_jobs.json` execute (§9).

---

## 4. Repository Structure

```text
repo/
  pyproject.toml          # uv-managed; exposes `sandbox` console script
  README.md

  sandbox/
    __init__.py           # re-exports run_date(), dry_run() helpers
    io.py                 # the 5 user-facing data functions (notebook-safe)
    cli.py                # `sandbox job init`
    runner.py             # discovery, execution, logging, one-time state
    validate.py           # PR validation script
    _template.py.tmpl     # job file template used by the CLI

    jobs/
      __init__.py
      daily/
        __init__.py
      one_time/
        __init__.py

  .github/
    workflows/
      validate-sandbox-jobs.yml
      run-sandbox-jobs.yml
```

Everything platform-side lives in four small modules. Do not split into sub-packages (`sandbox_api`, `sandbox_platform`) — keep it flat.

---

## 5. Job Contract

A job file is a Python module in `sandbox/jobs/daily/` or `sandbox/jobs/one_time/` containing:

```python
"""Daily customer summary sandbox table."""

OWNER = "analytics"
OUTPUT_TABLES = ["customer_summary"]


def main() -> None:
    from sandbox import io

    df = io.query("SELECT ...")
    summary = df.groupby("customer_id").agg(...)
    io.write_table(summary, "customer_summary")
```

Contract rules:

- Module docstring = description (required, non-empty).
- `OWNER: str` (required).
- `OUTPUT_TABLES: list[str]` (required; each name must pass table-name validation).
- `main()` synchronous callable with zero parameters (required).
- No queries or writes at import time — all logic inside `main()`.
- Job ID is the filename stem (e.g. `2026_06_10_backfill_customer_history`). Job type is the folder.

---

## 6. `sandbox/io.py` — User-Facing Data API

Must work in two environments with zero code changes:

- **Inside a job run** (GitHub Actions, env-configured AWS role).
- **In a Jupyter notebook** (user's local AWS credentials / SSO profile). `import sandbox.io` must never require runner context, env vars beyond standard AWS config, or raise at import time.

Functions (all backed by awswrangler):

```python
query(sql) -> pd.DataFrame
    # Run a read-only Athena SQL query against the configured workgroup and return a DataFrame.
    # Allow SELECT/WITH queries; reject obvious DDL/DML so Sandbox mutations go through write_table/delete_table.

read_table(table_name) -> pd.DataFrame
    # Convenience: SELECT * FROM sandbox_db.table_name.

write_table(df, table_name, if_exists="replace") -> None
    # Validate table name → wr.s3.to_parquet(..., path=s3://SANDBOX_BUCKET/sandbox-tables/{table_name}/,
    # dataset=True, database=SANDBOX_DB, table=table_name, mode=...)
    # Registers/updates the Glue table so it is immediately queryable in Athena.

list_tables() -> list[str]
    # Glue catalog listing for user Sandbox Tables in the sandbox database.
    # Hide reserved platform tables whose names begin with sandbox_.

delete_table(table_name, *, confirm=False) -> None
    # Delete S3 data + Glue table only when confirm=True. Refuses reserved platform table names, tables outside the sandbox database,
    # and any table whose Glue location is not under s3://SANDBOX_BUCKET/sandbox-tables/{table_name}/.
    # Available for notebooks/interactive cleanup; blocked during job runs.
```

Configuration is read lazily at function call time so `import sandbox.io` is notebook-safe and never fails due to missing `SANDBOX_*` values. Missing required configuration raises a clear framework exception naming the missing env var and operation. `SANDBOX_BUCKET` is a bucket name, not an S3 URI; values starting with `s3://` are rejected with a clear message.

Minimal custom exceptions:

```python
SandboxError
SandboxConfigError
SandboxValidationError
```

Configuration (env vars with sensible defaults, documented in README):

```text
SANDBOX_BUCKET        # S3 bucket name only, without s3://, for table data, logs, state
SANDBOX_DATABASE      # Glue database name for sandbox tables
SANDBOX_ATHENA_OUTPUT # s3 path for Athena query results (or workgroup default)
SANDBOX_WORKGROUP     # optional Athena workgroup
```

Table name validation: lowercase letters, digits, underscores; must start with a letter; max length 128 characters. Reject uppercase rather than normalizing it. Do not require owner/team prefixes or any `__` separator; table naming collision procedures will be handled by a later SOP. Treat `sandbox_` as a protected framework prefix: user-controlled table names and job IDs must not start with `sandbox_`, and user-facing writes/deletes reject reserved names with a clear error message.

Write tracking and enforcement: `write_table` reports each write (table name, row count, column count, columns, s3 path, if_exists mode) to an in-process recorder **if one is active** (the runner activates it), but only after the underlying `wr.s3.to_parquet` call succeeds. Failed writes are reflected in the Job Run `error_message`, not `table_writes`. During a job run, writes to tables not declared in that job's `OUTPUT_TABLES` fail with a clear error, and `delete_table` is blocked. In a notebook no recorder is active and declared-output enforcement does not apply. Implement as a module-level optional callback — do not over-engineer.

`dry_run` behavior: when `sandbox.dry_run()` is true (env var set by the workflow), job code still executes but Sandbox mutations are skipped. `write_table` records what it would write and skips the actual write; `delete_table` refuses to delete even with `confirm=True`. One-time completion state is not updated, and no job-run log record is appended to the log table. Dry-run summaries are printed to workflow output.

---

## 7. Runtime Helpers (replacing `ctx`)

In `sandbox/__init__.py`:

```python
def run_date() -> datetime.date
    # SANDBOX_RUN_DATE env var if set, else today (UTC).
    # Interpreted as the logical date the job is processing, not necessarily the wall-clock execution date.
    # If set, SANDBOX_RUN_DATE must be a real calendar date in YYYY-MM-DD format; invalid values raise a clear error.

def dry_run() -> bool
    # SANDBOX_DRY_RUN env var, default false.
    # Accept true/1/yes as true; false/0/no/unset as false; invalid values raise a clear error.
    # When true, job code runs but Sandbox mutations and one-time completion updates are skipped.
```

That is the entire runtime API surface for job authors. Values like run_id, commit SHA, and GitHub run ID are the **runner's** concern and go into the log record — jobs never see them.

---

## 8. CLI — `sandbox job init`

Registered as a console script so `uv run sandbox job init` works.

Prompts (with validation and re-prompt on invalid input):

```text
Job name        → becomes the filename stem / job ID
                  (lowercase letters, digits, underscores; max length 128;
                   may start with a digit; must not start with sandbox_;
                   suggest yyyy_mm_dd_ prefix for one-time jobs)
Job type        → daily / one_time
Owner           → free text, e.g. "analytics"
Output tables   → comma-separated; each validated with the io table-name rules
Description     → one line; becomes the module docstring
```

The CLI renders `_template.py.tmpl` and writes the file into the correct folder, refusing to overwrite an existing file. It prints the created path and a one-line "next step" hint.

The generated template contains the docstring, `OWNER`, `OUTPUT_TABLES`, and a `main()` with a small commented example (`query` → transform → `write_table`).

Keep the CLI dependency-light: `argparse` + `input()` is fine; a small library like `typer` is acceptable if it stays simple.

---

## 9. Runner (`sandbox/runner.py`)

Invoked by GitHub Actions:

```bash
uv run python -m sandbox.runner --type daily
uv run python -m sandbox.runner --type one_time
uv run python -m sandbox.runner --type daily --job <job_id>   # manual single run
```

Behavior:

1. Discover job files in the requested folder, then import and run one job at a time. Importing must be side-effect free per the contract; an import failure counts as a failed Job Run for that job and does not stop other jobs.
2. For `one_time`: load `s3://$SANDBOX_BUCKET/sandbox-platform/state/completed_jobs.json` (treat missing file as empty). If the state file exists but cannot be read or parsed, fail closed before running any one-time jobs. **Skip any job whose ID appears in it, even when a specific job is manually targeted.** This is what guarantees that merging a new one-time job runs only that job — previously succeeded jobs are skipped, and a previously *failed* job is retried on the next trigger. To rerun completed one-time work, create a new one-time job with a new filename/job ID or have an admin deliberately remove the completion marker.
3. Run each remaining job's `main()` inside try/except, with the write recorder configured to allow only the job's declared `OUTPUT_TABLES`. A failure does not stop other jobs.
4. After a one-time job succeeds, immediately add its ID (with timestamp, run_id, commit SHA) to `completed_jobs.json` and write it back to S3 — update after each job, not at the end, so a later crash can't lose markers. If writing completion state fails, stop running further one-time jobs and exit non-zero; the already-written job cannot be undone, but stopping reduces duplicate or ambiguous state. In dry runs, do not update completion state.
5. After any required one-time completion state update, and unless dry-run mode is enabled, write one log record per job attempt by appending to the **job runs log table**: a Parquet dataset at `s3://$SANDBOX_BUCKET/sandbox-platform/logs/job_runs/`, registered in Glue as `sandbox_job_runs`, partitioned by `run_date`, written with `wr.s3.to_parquet(dataset=True, mode="append", ...)`. Users can then query run history directly in Athena. In dry-run mode, print the would-be log summary to workflow output instead. Each Job Run gets its own unique `run_id`. The GitHub workflow run is captured separately as `github_run_id`, which can group multiple Job Runs. Record fields:

```text
run_id, job_id, job_type, owner, status (success | failed),
started_at, finished_at, duration_seconds, run_date,
declared_output_tables,
table_writes  (list of structs: table_name, row_count, column_count,
               columns, s3_path, if_exists),
commit_sha, github_run_id, github_actor, error_message
```

For import failures, `owner` is null, `declared_output_tables` is empty, and `error_message` contains the import error. For all failures, persist a concise error message in `error_message` and print the full traceback to workflow output. `started_at` and `finished_at` are UTC ISO-8601 strings such as `2026-06-25T14:03:22Z`. `duration_seconds` is an integer number of elapsed seconds, rounded to the nearest second with a minimum of 1 for non-zero runs. Validation should normally catch import failures before merge, but the runner handles them defensively.

Store `declared_output_tables` and `table_writes` as JSON string columns in v1. This keeps Athena/Parquet schema handling simple while preserving output/write details for inspection. Log writing must be best-effort: a logging failure is reported in workflow output but never changes job status or one-time state.

6. Exit non-zero if any job failed (so the workflow shows red), after all jobs were attempted.

---

## 10. Validation (`sandbox/validate.py`)

Run on PRs. For every file in `sandbox/jobs/daily/` and `sandbox/jobs/one_time/` (excluding `__init__.py`):

- Module imports cleanly (this alone catches most beginner mistakes).
- During import validation, all public `sandbox.io` functions (`query`, `read_table`, `write_table`, `list_tables`, `delete_table`) are temporarily blocked so import-time Sandbox IO fails with a clear message telling the author to move IO calls into `main()`.
- Non-empty module docstring.
- `OWNER` is a non-empty string.
- `OUTPUT_TABLES` is a non-empty list of valid table names.
- `main` exists, is callable, has zero parameters, and is not async.
- Filename stem is a valid job ID (lowercase letters, digits, underscores; max length 128; may start with a digit; must not start with `sandbox_`).
- Warn (not fail) if two jobs declare the same output table.

Print clear, friendly error messages naming the file and the problem. Exit non-zero on any failure.

---

## 11. GitHub Actions

### `validate-sandbox-jobs.yml`

- Trigger: `pull_request` touching `sandbox/**`.
- Steps: checkout → setup uv/python → `uv run python -m sandbox.validate`.
- No AWS access needed.
- No linting or formatting checks — out of scope for this project (planned separately later).

### `run-sandbox-jobs.yml`

- Triggers:
  - `schedule:` daily cron → runs with `--type daily`.
  - `push:` to `main` with paths `sandbox/jobs/one_time/**` → runs with `--type one_time`.
  - `workflow_dispatch:` with inputs `type` (daily/one_time), optional `job`, optional `run_date` (`YYYY-MM-DD`), and optional `dry_run` boolean → manual runs.
- `concurrency: group: sandbox-runner, cancel-in-progress: false` — serializes runs so concurrent updates to `completed_jobs.json` cannot clobber each other.
- Auth: `aws-actions/configure-aws-credentials` assuming an OIDC role (no long-lived keys).
- Steps: checkout → setup uv/python → configure AWS → set `SANDBOX_RUN_DATE` to the UTC workflow date for scheduled runs or the manual `run_date` input when provided → set `SANDBOX_DRY_RUN` from the manual `dry_run` input when provided, otherwise false → run `python -m sandbox.runner` with the resolved `--type` (and `--job` if provided). Export other `SANDBOX_*` env vars from repo/environment variables.

---

## 12. AWS Footprint

- **S3** (one bucket): user Sandbox Table data under `sandbox-tables/{table_name}/`, Athena query output, `sandbox-platform/logs/job_runs/` (Parquet log dataset), `sandbox-platform/state/completed_jobs.json` (single small state file).
- **Glue Data Catalog**: one sandbox database; tables registered/updated by awswrangler, including the platform-owned `sandbox_job_runs` log table.
- **Athena**: querying source data and sandbox tables.
- **IAM**: one OIDC-assumable role for GitHub Actions with least privilege: read source data, read/write the sandbox bucket prefixes, read/write Glue metadata for the sandbox database, run Athena queries. Notebook users use their own existing AWS credentials.

Infrastructure setup (bucket, database, role) is assumed to exist or be created manually; do not build IaC in this project.

---

## 13. Implementation Phases

### Phase 1 — Package, io module, CLI

- `pyproject.toml` with uv, awswrangler, pandas; `sandbox` console script.
- `io.py` with all five functions + table-name validation + env config + optional write recorder + dry-run handling.
- `run_date()` / `dry_run()` helpers.
- `cli.py` + `_template.py.tmpl`; `uv run sandbox job init` creates a valid job file in the right folder.
- Unit tests for table-name validation, template rendering, and helpers (mock AWS; do not require live AWS for tests).

**Done when:** a developer can run `sandbox job init`, get a working template, and (with AWS creds) use `io` functions from a notebook.

### Phase 2 — Validation

- `validate.py` with the checks in §10.
- `validate-sandbox-jobs.yml`.
- Tests: fixture jobs (valid, missing OWNER, bad table name, missing main, import-time Sandbox IO side effect) and assertions on validator output.

**Done when:** a PR with a malformed job fails with a clear message; a valid job passes.

### Phase 3 — Runner + workflows

- `runner.py` per §9 (discovery, execution, log-table appends, one-time state file, exit codes).
- `run-sandbox-jobs.yml` per §11 with all three triggers and the concurrency group.
- Tests: runner with mocked AWS — one-time skip logic, state updates after each success, failure isolation, undeclared output writes fail, log record contents, logging failures not affecting job status.

**Done when:** daily jobs run on schedule; merging a new one-time job runs only that job and it is skipped on subsequent triggers; run history is queryable via `sandbox_job_runs` in Athena.

### Phase 4 — Docs

- README snippets for one example Daily Job and one example One-Time Job. Do not add example job files under `sandbox/jobs/`; real job folders should contain only jobs intended to run.
- README: quickstart (init → fill in → PR → merged → query in Athena), notebook usage of `io`, env vars, table naming rules, run date semantics, rerun semantics, how one-time skipping works, how to manually trigger a run.

**Done when:** a new user can go from zero to a merged, running job using only the README.

---

## 14. Future Considerations / Explicitly Deferred

These are intentionally outside v1. Do not implement them unless the plan is explicitly revised.

- Table naming collision SOP and any related ownership process.
- First-class partitioned Sandbox Table support, such as a `partition_cols` argument to `write_table`. In v1, users who need date-based querying should include the date as a normal column rather than hand-rolling physical partitions.
- Linting and formatting checks in validation workflows.
- Infrastructure as Code for S3 buckets, Glue databases, Athena workgroups, or IAM roles.
- Declared delete support inside Jobs, if Jobs ever need to remove Sandbox Tables safely.
- Max-attempt, quarantine, or auto-disable behavior for repeatedly failing One-Time Jobs.

## 15. Rules for Job Authors (goes in README)

1. Create jobs with `uv run sandbox job init` — don't hand-create files.
2. Recurring work goes in `sandbox/jobs/daily/`; run-once work goes in `sandbox/jobs/one_time/`.
3. All logic lives inside `main()`; nothing runs at import time.
4. Use `io.write_table()` for all sandbox outputs.
5. Make daily jobs safe to rerun (default `if_exists="replace"` helps).
6. Never reuse an old one-time job's filename — new backfill or rerun, new file. `completed_jobs.json` is platform state, not a user API.
7. Don't write to a table owned by another job unless agreed.
8. Keep jobs small enough for a GitHub Actions runner.

---

## 16. Success Criteria

- `uv run sandbox job init` creates a correct starter job in the right folder.
- A malformed job cannot merge (validation fails the PR with a clear message).
- Daily jobs run automatically every day.
- A newly merged one-time job runs exactly once; previously completed one-time jobs are never re-run; previously failed ones are retried.
- Every job run appends one record to `sandbox_job_runs`, including table write details, and users can query run history with SQL in Athena.
- The `io` module works identically in jobs and in notebooks.
- A new user can self-serve from the README alone.