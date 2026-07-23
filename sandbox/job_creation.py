"""Job-definition validation and file creation."""

import re
from pathlib import Path

from sandbox.exceptions import SandboxValidationError

_TEMPLATE_PATH = Path(__file__).parent / "_template.py.tmpl"


def validate_job_name(name: str) -> str | None:
    """Return an error string if a job name is invalid, otherwise None."""
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


def validate_table_names(raw: str) -> tuple[list[str] | None, str | None]:
    """Parse and validate comma-separated table names. Returns (names, error)."""
    from sandbox.io import _validate_table_name

    names = [name.strip() for name in raw.split(",") if name.strip()]
    if not names:
        return None, "At least one output table is required."
    for name in names:
        try:
            _validate_table_name(name)
        except SandboxValidationError as exc:
            return None, str(exc)
    return names, None


def _render_template(description: str, owner: str, output_tables: list[str]) -> str:
    template = _TEMPLATE_PATH.read_text()
    tables_repr = ", ".join(f'"{table}"' for table in output_tables)
    return (
        template.replace("{{ description }}", description)
        .replace("{{ owner }}", owner)
        .replace("{{ output_tables }}", tables_repr)
        .replace("{{ first_table }}", output_tables[0])
    )


def create_job(
    jobs_root: Path,
    job_name: str,
    job_type: str,
    owner: str,
    output_tables: list[str],
    description: str,
) -> Path:
    """Render a job template, refusing to overwrite an existing file."""
    destination = jobs_root / job_type / f"{job_name}.py"
    if destination.exists():
        raise FileExistsError(f"Job file already exists: {destination}")
    content = _render_template(description, owner, output_tables)
    destination.write_text(content, encoding="utf-8")
    return destination
