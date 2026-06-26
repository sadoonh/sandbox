import datetime
import os

from sandbox.exceptions import SandboxConfigError


def run_date() -> datetime.date:
    value = os.environ.get("SANDBOX_RUN_DATE")
    if value is None:
        return datetime.datetime.now(datetime.timezone.utc).date()
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise SandboxConfigError(
            f"SANDBOX_RUN_DATE must be a valid date in YYYY-MM-DD format, got: {value!r}"
        )


def dry_run() -> bool:
    value = os.environ.get("SANDBOX_DRY_RUN", "").strip().lower()
    if value in ("", "false", "0", "no"):
        return False
    if value in ("true", "1", "yes"):
        return True
    raise SandboxConfigError(
        f"SANDBOX_DRY_RUN must be true/1/yes or false/0/no, got: {os.environ.get('SANDBOX_DRY_RUN')!r}"
    )
