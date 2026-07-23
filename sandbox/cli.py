"""CLI: create, run, list, and validate sandbox jobs."""

import argparse
import ast
import datetime
import os
import sys
from pathlib import Path

from tabulate import tabulate

from sandbox.job_creation import (
    create_job,
    validate_job_name as _validate_job_name,
    validate_table_names as _validate_table_names,
)

JOBS_ROOT = Path(__file__).parent / "jobs"
_JOB_SCHEDULES = {
    "daily": "Daily 09:00 UTC",
    "one_time": "After merge (once)",
}


class _CompactHelpFormatter(argparse.HelpFormatter):
    """Render terse, consistently capitalized usage text."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=30)

    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, "Usage: ")

    def _format_action(self, action):
        if isinstance(action, argparse._SubParsersAction):
            return "".join(
                super(_CompactHelpFormatter, self)._format_action(subaction)
                for subaction in action._get_subactions()
            )
        return super()._format_action(action)


class _CompactArgumentParser(argparse.ArgumentParser):
    """Argument parser with minimal help headings and text."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs["add_help"] = False
        kwargs.setdefault("formatter_class", _CompactHelpFormatter)
        super().__init__(*args, **kwargs)
        self._positionals.title = "Arguments"
        self._optionals.title = "Options"
        self.add_argument("-h", "--help", action="help", help="Show help.")

    def add_subparsers(self, **kwargs):
        self._positionals.title = "Commands"
        return super().add_subparsers(**kwargs)


def cmd_init(jobs_root: Path | None = None) -> bool:
    """Run the interactive job-creation wizard."""
    from sandbox.job_wizard import run_job_init

    return run_job_init(jobs_root or JOBS_ROOT)


def _read_job_owner(path: Path) -> str:
    """Read a job's literal OWNER without importing or executing the job."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return "<unknown>"

    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "OWNER" for target in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "OWNER"
        ):
            value = node.value

        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value

    return "<unknown>"


def _display_location(path: Path, jobs_root: Path) -> str:
    """Return a compact location relative to the jobs directory."""
    try:
        return str(path.relative_to(jobs_root))
    except ValueError:
        return str(path)


def cmd_list_jobs(jobs_root: Path | None = None) -> None:
    """Print the available jobs and their scheduling metadata."""
    root = jobs_root or JOBS_ROOT
    rows: list[tuple[str, str, str, str, str]] = []

    for job_type in ("daily", "one_time"):
        folder = root / job_type
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.py")):
            if path.name == "__init__.py":
                continue
            rows.append(
                (
                    path.stem,
                    job_type,
                    _read_job_owner(path),
                    _display_location(path, root),
                    _JOB_SCHEDULES[job_type],
                )
            )

    if not rows:
        print("No sandbox jobs found.")
        return

    headers = ("JOB", "TYPE", "AUTHOR", "LOCATION", "RUNS")
    print(tabulate(rows, headers=headers, tablefmt="rounded_grid"))


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


def _build_parser() -> argparse.ArgumentParser:
    parser = _CompactArgumentParser(
        prog="sandbox",
        usage="sandbox {job,list,validate}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    job_parser = subparsers.add_parser(
        "job",
        help="Create or run jobs.",
        usage="sandbox job {init,run}",
    )
    job_subparsers = job_parser.add_subparsers(dest="job_command", required=True)
    job_subparsers.add_parser(
        "init",
        help="Create a job.",
        usage="sandbox job init",
    )

    run_parser = job_subparsers.add_parser(
        "run",
        help="Run a job locally.",
        usage="sandbox job run JOB_ID [--dry-run] [--run-date YYYY-MM-DD]",
    )
    run_parser.add_argument("job_id", metavar="JOB_ID", help="Job filename without .py.")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip writes and state updates.",
    )
    run_parser.add_argument(
        "--run-date",
        metavar="YYYY-MM-DD",
        help="Set the logical run date.",
    )

    list_parser = subparsers.add_parser(
        "list",
        help="List resources.",
        usage="sandbox list {job}",
    )
    list_subparsers = list_parser.add_subparsers(dest="list_command", required=True)
    list_subparsers.add_parser(
        "job",
        help="List job metadata.",
        usage="sandbox list job",
    )

    subparsers.add_parser(
        "validate",
        help="Validate job files.",
        usage="sandbox validate",
    )
    return parser


def main() -> None:
    from sandbox._helpers import load_env_file

    load_env_file()
    args = _build_parser().parse_args()

    if args.command == "job" and args.job_command == "init":
        sys.exit(0 if cmd_init() else 1)
    elif args.command == "job" and args.job_command == "run":
        success = cmd_run(args.job_id, dry_run=args.dry_run, run_date=args.run_date)
        sys.exit(0 if success else 1)
    elif args.command == "list" and args.list_command == "job":
        cmd_list_jobs()
    elif args.command == "validate":
        from sandbox.validate import main as validate_main
        validate_main()
