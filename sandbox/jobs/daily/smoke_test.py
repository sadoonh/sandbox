"""Smoke test: writes a tiny static table to verify the end-to-end pipeline."""

OWNER = "platform"
OUTPUT_TABLES = ["smoke_test"]


def main() -> None:
    import pandas as pd

    from sandbox import io, run_date

    df = pd.DataFrame(
        {
            "run_date": [str(run_date())],
            "message": ["hello from sandbox_io"],
            "value": [42],
        }
    )
    io.write_table(df, "smoke_test")
