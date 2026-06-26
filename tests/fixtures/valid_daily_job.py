"""Daily customer summary."""

OWNER = "analytics"
OUTPUT_TABLES = ["customer_summary"]


def main() -> None:
    from sandbox import io
    df = io.query("SELECT 1")
    io.write_table(df, "customer_summary")
