"""CLI: `sandbox job init`, `sandbox job run`, `sandbox validate`."""

import argparse
import datetime
import os
import re
import sys
from pathlib import Path

from sandbox.exceptions import SandboxValidationError

JOBS_ROOT = Path(__file__).parent / "jobs"
_TEMPLATE_PATH = Path(__file__).parent / "_template.py.tmpl"


def _validate_job_name(name: str) -> str | None:
    """Return an error string if name is invalid, else None."""
    if not name:
        return "Job name cannot be empty."
    if len(name) > 128:
        return "Job name must be 128 characters or fewer."
    if name.startswith("sandbox_"):
        return "Job name must not start with 'sandbox_' (reserved prefix)."
    if any(c.isupper() for c in name):
        return "Job name must be lowercase."
    if not re.match(r"^[a-z0-9][a-z0-9_]*$", name):
        return (
            "Job name may only contain lowercase letters, digits, and underscores. "
            "It may start with a letter or digit."
        )
    return None


def _validate_table_names(raw: str) -> tuple[list[str] | None, str | None]:
    """Parse and validate comma-separated table names. Returns (names, error)."""
    from sandbox.io import _validate_table_name

    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        return None, "At least one output table is required."
    for name in names:
        try:
            _validate_table_name(name)
        except SandboxValidationError as e:
            return None, str(e)
    return names, None


def _render_template(
    description: str,
    owner: str,
    output_tables: list[str],
) -> str:
    template = _TEMPLATE_PATH.read_text()
    tables_repr = ", ".join(f'"{t}"' for t in output_tables)
    first_table = output_tables[0]
    return (
        template
        .replace("{{ description }}", description)
        .replace("{{ owner }}", owner)
        .replace("{{ output_tables }}", tables_repr)
        .replace("{{ first_table }}", first_table)
    )


def create_job(
    jobs_root: Path,
    job_name: str,
    job_type: str,
    owner: str,
    output_tables: list[str],
    description: str,
) -> Path:
    """Render template and write job file. Raises FileExistsError if already exists."""
    dest = jobs_root / job_type / f"{job_name}.py"
    if dest.exists():
        raise FileExistsError(f"Job file already exists: {dest}")
    content = _render_template(description, owner, output_tables)
    dest.write_text(content, encoding="utf-8")
    return dest


def cmd_init(jobs_root: Path | None = None) -> None:
    root = jobs_root or JOBS_ROOT

    def prompt(message: str, validate=None) -> str:
        while True:
            value = input(message).strip()
            if validate:
                error = validate(value)
                if error:
                    print(f"  Error: {error}", file=sys.stderr)
                    continue
            return value

    def require_nonempty(v: str) -> str | None:
        return None if v else "This field is required."

    job_name = prompt("Job name: ", _validate_job_name)

    def validate_type(v: str) -> str | None:
        return None if v in ("daily", "one_time") else "Enter 'daily' or 'one_time'."

    job_type = prompt("Job type (daily/one_time): ", validate_type)
    owner = prompt("Owner: ", require_nonempty)

    tables_raw = prompt(
        "Output tables (comma-separated): ",
        lambda v: _validate_table_names(v)[1],
    )
    output_tables, _ = _validate_table_names(tables_raw)

    description = prompt("Description (one line): ", require_nonempty)

    path = create_job(
        jobs_root=root,
        job_name=job_name,
        job_type=job_type,
        owner=owner,
        output_tables=output_tables,
        description=description,
    )
    print(f"Created: {path}")
    print(f"Next step: open {path.name} and fill in main().")


def cmd_run(
    job_id: str,
    *,
    dry_run: bool = False,
    run_date: str | None = None,
    jobs_root: Path | None = None,
) -> bool:
    """Run a single job locally. Returns True on success."""
    from sandbox import runner

    found = [
        job_type
        for job_type in ("daily", "one_time")
        if runner._discover_jobs(job_type, jobs_root, job_id)
    ]
    if not found:
        print(f"Error: no job found with ID {job_id!r}.", file=sys.stderr)
        return False
    if len(found) > 1:
        print(
            f"Error: job ID {job_id!r} exists in both daily/ and one_time/ — rename one.",
            file=sys.stderr,
        )
        return False

    if run_date is not None:
        try:
            datetime.date.fromisoformat(run_date)
        except ValueError:
            print(f"Error: --run-date must be YYYY-MM-DD, got {run_date!r}.", file=sys.stderr)
            return False
        os.environ["SANDBOX_RUN_DATE"] = run_date
    if dry_run:
        os.environ["SANDBOX_DRY_RUN"] = "true"

    return runner.run(found[0], job_id=job_id, jobs_root=jobs_root)


def main() -> None:
    from sandbox._helpers import load_env_file

    load_env_file()

    parser = argparse.ArgumentParser(prog="sandbox", description="Sandbox job framework CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    job_parser = subparsers.add_parser("job", help="Manage sandbox jobs.")
    job_subparsers = job_parser.add_subparsers(dest="job_command", required=True)

    job_subparsers.add_parser("init", help="Interactively create a new job file.")

    run_parser = job_subparsers.add_parser("run", help="Run a single job locally.")
    run_parser.add_argument("job_id", help="Job ID (filename stem).")
    run_parser.add_argument(
        "--dry-run", action="store_true",
        help="Run job code without writing tables or updating state.",
    )
    run_parser.add_argument(
        "--run-date",
        help="Override the logical run date (YYYY-MM-DD).",
    )

    subparsers.add_parser("validate", help="Validate all job files (same check CI runs).")

    args = parser.parse_args()

    if args.command == "job" and args.job_command == "init":
        cmd_init()
    elif args.command == "job" and args.job_command == "run":
        success = cmd_run(args.job_id, dry_run=args.dry_run, run_date=args.run_date)
        sys.exit(0 if success else 1)
    elif args.command == "validate":
        from sandbox.validate import main as validate_main
        validate_main()
