"""One-time job that writes a deterministic dummy DataFrame for testing."""

OWNER = "platform"
OUTPUT_TABLES = ["dummy_dataframe"]


def main() -> None:
    import pandas as pd

    from sandbox import io

    df = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "name": ["alpha", "beta", "gamma"],
            "value": [10, 20, 30],
        }
    )
    io.write_table(df, "dummy_dataframe")
