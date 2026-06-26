"""Job runner: discovery, execution, one-time state, log writing."""

import argparse
import importlib.util
import json
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import awswrangler as wr
import boto3
import pandas as pd

from sandbox._helpers import dry_run, run_date
from sandbox import io
from sandbox.exceptions import SandboxError


def _load_completed_jobs(bucket: str) -> dict:
    s3 = boto3.client("s3")
    key = "sandbox-platform/state/completed_jobs.json"
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read())
    except s3.exceptions.NoSuchKey:
        return {}
    except Exception as exc:
        raise SandboxError(
            f"Failed to read one-time job state from s3://{bucket}/{key}: {exc}"
        ) from exc


def _save_completed_jobs(bucket: str, data: dict) -> None:
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=bucket,
        Key="sandbox-platform/state/completed_jobs.json",
        Body=json.dumps(data, indent=2).encode(),
        ContentType="application/json",
    )


def _discover_jobs(
    job_type: str,
    jobs_root: Path | None = None,
    job_id: str | None = None,
) -> list[Path]:
    root = jobs_root or Path(__file__).parent / "jobs"
    folder = root / job_type
    if not folder.exists():
        return []
    paths = sorted(p for p in folder.glob("*.py") if p.name != "__init__.py")
    if job_id:
        paths = [p for p in paths if p.stem == job_id]
    return paths


def _import_job(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_log_record(bucket: str, database: str, record: dict) -> None:
    df = pd.DataFrame([record])
    wr.s3.to_parquet(
        df,
        path=f"s3://{bucket}/sandbox-platform/logs/job_runs/",
        dataset=True,
        database=database,
        table="sandbox_job_runs",
        mode="append",
        schema_evolution=True,
        partition_cols=["run_date"],
    )


def _try_write_log(bucket: str, database: str, record: dict, is_dry_run: bool) -> None:
    if is_dry_run:
        print(
            f"[DRY RUN] Would log: job={record.get('job_id')} "
            f"status={record.get('status')} "
            f"writes={record.get('table_writes')}"
        )
        return
    if not bucket or not database:
        return
    try:
        _write_log_record(bucket, database, record)
    except Exception as exc:
        print(f"WARNING: Failed to write log record for {record.get('job_id')!r}: {exc}", file=sys.stderr)


def _utc_now_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(
    job_type: str,
    job_id: str | None = None,
    jobs_root: Path | None = None,
) -> bool:
    """Run all jobs of the given type. Returns True if all succeeded."""
    bucket = os.environ.get("SANDBOX_BUCKET", "")
    database = os.environ.get("SANDBOX_DATABASE", "")
    github_run_id = os.environ.get("SANDBOX_GITHUB_RUN_ID", "")
    github_actor = os.environ.get("SANDBOX_GITHUB_ACTOR", "")
    commit_sha = os.environ.get("SANDBOX_COMMIT_SHA", "")
    the_run_date = str(run_date())
    is_dry_run = dry_run()

    completed_jobs: dict = {}
    if job_type == "one_time":
        completed_jobs = _load_completed_jobs(bucket)

    paths = _discover_jobs(job_type, jobs_root, job_id)
    all_succeeded = True

    for path in paths:
        jid = path.stem

        if job_type == "one_time" and jid in completed_jobs:
            continue

        started_at = datetime.now(timezone.utc)
        run_id = str(uuid.uuid4())
        owner = None
        declared_output_tables: list[str] = []
        status = "failed"
        error_message: str | None = None
        writes: list[dict] = []

        # --- Import ---
        try:
            module = _import_job(path)
        except Exception as exc:
            error_message = f"Import failed: {exc}"
            traceback.print_exc()
            all_succeeded = False
            finished_at = datetime.now(timezone.utc)
            _try_write_log(bucket, database, {
                "run_id": run_id,
                "job_id": jid,
                "job_type": job_type,
                "owner": None,
                "status": "failed",
                "started_at": _utc_now_str(started_at),
                "finished_at": _utc_now_str(finished_at),
                "duration_seconds": max(1, round((finished_at - started_at).total_seconds())),
                "run_date": the_run_date,
                "declared_output_tables": "[]",
                "table_writes": "[]",
                "commit_sha": commit_sha,
                "github_run_id": github_run_id,
                "github_actor": github_actor,
                "error_message": error_message,
            }, is_dry_run)
            continue

        owner = getattr(module, "OWNER", None)
        declared_output_tables = getattr(module, "OUTPUT_TABLES", [])

        # --- Execute ---
        writes = io._start_job_run(declared_output_tables)
        try:
            module.main()
            status = "success"
        except Exception as exc:
            status = "failed"
            error_message = str(exc)
            traceback.print_exc()
            all_succeeded = False
        finally:
            io._end_job_run()

        finished_at = datetime.now(timezone.utc)
        duration = max(1, round((finished_at - started_at).total_seconds()))

        # --- Update one-time state after each success ---
        if job_type == "one_time" and status == "success" and not is_dry_run:
            completed_jobs[jid] = {
                "timestamp": _utc_now_str(finished_at),
                "run_id": run_id,
                "commit_sha": commit_sha,
            }
            try:
                _save_completed_jobs(bucket, completed_jobs)
            except Exception as exc:
                print(
                    f"ERROR: Failed to save completion state for {jid!r}: {exc}. "
                    "Stopping further one-time jobs to avoid ambiguous state.",
                    file=sys.stderr,
                )
                _try_write_log(bucket, database, _build_record(
                    run_id, jid, job_type, owner, status, started_at, finished_at,
                    duration, the_run_date, declared_output_tables, writes,
                    commit_sha, github_run_id, github_actor, None,
                ), is_dry_run)
                sys.exit(1)

        # --- Write log record (best-effort) ---
        _try_write_log(bucket, database, _build_record(
            run_id, jid, job_type, owner, status, started_at, finished_at,
            duration, the_run_date, declared_output_tables, writes,
            commit_sha, github_run_id, github_actor, error_message,
        ), is_dry_run)

    return all_succeeded


def _build_record(
    run_id, job_id, job_type, owner, status,
    started_at, finished_at, duration, the_run_date,
    declared_output_tables, writes,
    commit_sha, github_run_id, github_actor, error_message,
) -> dict:
    return {
        "run_id": run_id,
        "job_id": job_id,
        "job_type": job_type,
        "owner": owner,
        "status": status,
        "started_at": _utc_now_str(started_at),
        "finished_at": _utc_now_str(finished_at),
        "duration_seconds": duration,
        "run_date": the_run_date,
        "declared_output_tables": json.dumps(declared_output_tables),
        "table_writes": json.dumps(writes),
        "commit_sha": commit_sha,
        "github_run_id": github_run_id,
        "github_actor": github_actor,
        "error_message": error_message,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run sandbox jobs.")
    parser.add_argument("--type", required=True, choices=["daily", "one_time"])
    parser.add_argument("--job", help="Run a specific job by ID.")
    args = parser.parse_args()
    success = run(args.type, job_id=args.job)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
