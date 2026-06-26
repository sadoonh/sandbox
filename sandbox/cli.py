"""CLI: `sandbox job init`."""

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


def main() -> None:
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "job" and args[1] == "init":
        cmd_init()
    else:
        print("Usage: sandbox job init", file=sys.stderr)
        sys.exit(1)
