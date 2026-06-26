"""User-facing data API. Safe to import in notebooks and job files."""

import os
import re
from contextlib import contextmanager
from typing import Callable

import awswrangler as wr
import boto3
import pandas as pd

from sandbox._helpers import dry_run
from sandbox.exceptions import SandboxConfigError, SandboxError, SandboxValidationError

# ---------------------------------------------------------------------------
# Module-level state (managed by runner; inactive in notebooks)
# ---------------------------------------------------------------------------

_validation_mode: bool = False
_write_recorder: Callable[[dict], None] | None = None
_allowed_tables: frozenset[str] | None = None
_in_job_run: bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_config(key: str, operation: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise SandboxConfigError(
            f"Missing required configuration for {operation!r}: set the {key} environment variable."
        )
    if key == "SANDBOX_BUCKET" and value.startswith("s3://"):
        raise SandboxConfigError(
            f"SANDBOX_BUCKET must be a bucket name without s3://, got: {value!r}"
        )
    return value


def _validate_table_name(name: str) -> None:
    if not name:
        raise SandboxValidationError("Table name cannot be empty.")
    if name.startswith("sandbox_"):
        raise SandboxValidationError(
            f"Table name {name!r} uses reserved prefix 'sandbox_' — reserved for platform use."
        )
    if len(name) > 128:
        raise SandboxValidationError(
            f"Table name {name!r} exceeds the 128-character limit."
        )
    if any(c.isupper() for c in name):
        raise SandboxValidationError(
            f"Table name {name!r} contains uppercase letters — use lowercase only."
        )
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        raise SandboxValidationError(
            f"Table name {name!r} is invalid — use lowercase letters, digits, and underscores, "
            "starting with a letter."
        )


def _check_not_validation_mode() -> None:
    if _validation_mode:
        raise SandboxError(
            "sandbox.io functions cannot be called at import time — "
            "move all IO calls inside main()."
        )


@contextmanager
def _validation_context():
    global _validation_mode
    _validation_mode = True
    try:
        yield
    finally:
        _validation_mode = False


def _start_job_run(allowed_tables: list[str]) -> list[dict]:
    global _write_recorder, _allowed_tables, _in_job_run
    writes: list[dict] = []
    _allowed_tables = frozenset(allowed_tables)
    _in_job_run = True
    _write_recorder = writes.append
    return writes


def _end_job_run() -> None:
    global _write_recorder, _allowed_tables, _in_job_run
    _write_recorder = None
    _allowed_tables = None
    _in_job_run = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def query(sql: str) -> pd.DataFrame:
    """Run a read-only Athena SQL query and return a DataFrame."""
    _check_not_validation_mode()
    database = os.environ.get("SANDBOX_DATABASE")
    workgroup = os.environ.get("SANDBOX_WORKGROUP")
    s3_output = os.environ.get("SANDBOX_ATHENA_OUTPUT")
    kwargs: dict = {}
    if database:
        kwargs["database"] = database
    if workgroup:
        kwargs["workgroup"] = workgroup
    if s3_output:
        kwargs["s3_output"] = s3_output
    return wr.athena.read_sql_query(sql, **kwargs)


def read_table(table_name: str) -> pd.DataFrame:
    """Return all rows from a sandbox table as a DataFrame."""
    _check_not_validation_mode()
    _validate_table_name(table_name)
    database = _require_config("SANDBOX_DATABASE", "read_table")
    return query(f"SELECT * FROM {database}.{table_name}")


def write_table(df: pd.DataFrame, table_name: str, if_exists: str = "replace") -> None:
    """Write a DataFrame to a sandbox table, registering it in Glue."""
    _check_not_validation_mode()
    _validate_table_name(table_name)
    if if_exists not in ("replace", "append"):
        raise SandboxValidationError(
            f"if_exists must be 'replace' or 'append', got: {if_exists!r}"
        )

    if _in_job_run and _allowed_tables is not None and table_name not in _allowed_tables:
        raise SandboxValidationError(
            f"Job attempted to write to {table_name!r} which is not declared in OUTPUT_TABLES. "
            "Add it to OUTPUT_TABLES before writing."
        )

    bucket = _require_config("SANDBOX_BUCKET", "write_table")
    database = _require_config("SANDBOX_DATABASE", "write_table")
    s3_path = f"s3://{bucket}/sandbox-tables/{table_name}/"
    mode = "overwrite" if if_exists == "replace" else if_exists

    if dry_run():
        write_info: dict = {
            "table_name": table_name,
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": list(df.columns),
            "s3_path": s3_path,
            "if_exists": if_exists,
            "dry_run": True,
        }
        if _write_recorder is not None:
            _write_recorder(write_info)
        print(f"[DRY RUN] Would write {len(df)} rows to sandbox table {table_name!r} at {s3_path}")
        return

    wr.s3.to_parquet(
        df,
        path=s3_path,
        dataset=True,
        database=database,
        table=table_name,
        mode=mode,
    )

    if _write_recorder is not None:
        _write_recorder({
            "table_name": table_name,
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": list(df.columns),
            "s3_path": s3_path,
            "if_exists": if_exists,
        })


def list_tables() -> list[str]:
    """List user sandbox tables (hides platform tables prefixed with sandbox_)."""
    _check_not_validation_mode()
    database = _require_config("SANDBOX_DATABASE", "list_tables")
    # get_tables() is an unbounded generator; wr.catalog.tables() silently caps at 100.
    return [
        table["Name"]
        for table in wr.catalog.get_tables(database=database)
        if not table["Name"].startswith("sandbox_")
    ]


def delete_table(table_name: str, *, confirm: bool = False) -> None:
    """Delete a sandbox table's S3 data and Glue entry."""
    _check_not_validation_mode()

    if _in_job_run:
        raise SandboxError("delete_table() is not available during a job run.")

    if not confirm:
        raise SandboxValidationError(
            "Pass confirm=True to delete_table() to confirm deletion."
        )

    _validate_table_name(table_name)
    bucket = _require_config("SANDBOX_BUCKET", "delete_table")
    database = _require_config("SANDBOX_DATABASE", "delete_table")

    glue = boto3.client("glue")
    try:
        response = glue.get_table(DatabaseName=database, Name=table_name)
    except glue.exceptions.EntityNotFoundException:
        raise SandboxValidationError(
            f"Table {table_name!r} does not exist in database {database!r}."
        )
    location: str = response["Table"]["StorageDescriptor"]["Location"]

    expected_prefix = f"s3://{bucket}/sandbox-tables/"
    if not location.startswith(expected_prefix):
        raise SandboxValidationError(
            f"Table {table_name!r} is not a sandbox table (location: {location!r}). "
            "Only tables under sandbox-tables/ can be deleted via delete_table()."
        )

    wr.s3.delete_objects(path=location)
    wr.catalog.delete_table_if_exists(database=database, table=table_name)
