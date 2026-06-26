"""IO called at import time — not allowed."""

OWNER = "analytics"
OUTPUT_TABLES = ["my_table"]

from sandbox import io
_df = io.query("SELECT 1")  # import-time IO


def main() -> None:
    pass
