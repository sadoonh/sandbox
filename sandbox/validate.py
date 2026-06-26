"""PR validation: checks all job files in sandbox/jobs/ for correctness."""

import importlib.util
import inspect
import re
import sys
from collections import defaultdict
from pathlib import Path

from sandbox import io as sandbox_io
from sandbox.exceptions import SandboxValidationError

_VALID_JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,127}$")


class ValidationError(Exception):
    pass


def _validate_job_file(path: Path) -> list[str]:
    """Validate a single job file. Returns a list of error strings."""
    errors: list[str] = []
    job_id = path.stem

    # Filename / job ID validation
    if job_id.startswith("sandbox_"):
        errors.append(
            f"{path.name}: job ID {job_id!r} must not start with 'sandbox_' (reserved prefix)."
        )
    elif not _VALID_JOB_ID_RE.match(job_id):
        errors.append(
            f"{path.name}: job ID {job_id!r} is invalid — use lowercase letters, digits, "
            "and underscores."
        )

    # Import the module with validation mode active (blocks IO at import time)
    with sandbox_io._validation_context():
        try:
            spec = importlib.util.spec_from_file_location(job_id, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:
            errors.append(f"{path.name}: import failed — {exc}")
            return errors

    # Module docstring
    doc = inspect.getdoc(module)
    if not doc:
        errors.append(f"{path.name}: missing or empty module docstring (required as description).")

    # OWNER
    owner = getattr(module, "OWNER", None)
    if owner is None:
        errors.append(f"{path.name}: missing OWNER attribute.")
    elif not isinstance(owner, str) or not owner.strip():
        errors.append(f"{path.name}: OWNER must be a non-empty string.")

    # OUTPUT_TABLES
    output_tables = getattr(module, "OUTPUT_TABLES", None)
    if output_tables is None:
        errors.append(f"{path.name}: missing OUTPUT_TABLES attribute.")
    elif not isinstance(output_tables, list) or len(output_tables) == 0:
        errors.append(f"{path.name}: OUTPUT_TABLES must be a non-empty list.")
    else:
        for table in output_tables:
            try:
                from sandbox.io import _validate_table_name
                _validate_table_name(table)
            except SandboxValidationError as e:
                errors.append(f"{path.name}: OUTPUT_TABLES contains invalid table name — {e}")

    # main()
    main_fn = getattr(module, "main", None)
    if main_fn is None or not callable(main_fn):
        errors.append(f"{path.name}: missing callable main() function.")
    else:
        if inspect.iscoroutinefunction(main_fn):
            errors.append(f"{path.name}: main() must not be async.")
        sig = inspect.signature(main_fn)
        params = [
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        ]
        if len(sig.parameters) > 0:
            errors.append(
                f"{path.name}: main() must have zero parameters "
                f"(found: {list(sig.parameters)})."
            )

    return errors


def validate(jobs_root: Path | None = None, *, raise_on_failure: bool = False) -> list[str]:
    """Validate all job files. Returns list of error strings (empty = all good)."""
    if jobs_root is None:
        jobs_root = Path(__file__).parent / "jobs"

    all_errors: list[str] = []
    # table_name -> list of job files declaring it
    table_owners: dict[str, list[str]] = defaultdict(list)

    for folder in ("daily", "one_time"):
        folder_path = jobs_root / folder
        if not folder_path.exists():
            continue
        for path in sorted(folder_path.glob("*.py")):
            if path.name == "__init__.py":
                continue
            errors = _validate_job_file(path)
            all_errors.extend(errors)

            # Collect output table declarations for duplicate check (only if file is otherwise valid)
            if not errors:
                module_id = path.stem
                spec = importlib.util.spec_from_file_location(module_id, path)
                module = importlib.util.module_from_spec(spec)
                with sandbox_io._validation_context():
                    spec.loader.exec_module(module)
                for table in getattr(module, "OUTPUT_TABLES", []):
                    table_owners[table].append(path.name)

    # Duplicate output table check
    for table, owners in table_owners.items():
        if len(owners) > 1:
            all_errors.append(
                f"Duplicate OUTPUT_TABLES entry {table!r} declared by: {', '.join(owners)}. "
                "Each table may only be declared by one job."
            )

    if raise_on_failure and all_errors:
        raise ValidationError("\n".join(all_errors))

    return all_errors


def main() -> None:
    import sys
    errors = validate(raise_on_failure=False)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    print("All sandbox jobs validated successfully.")


if __name__ == "__main__":
    main()
