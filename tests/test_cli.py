"""Tests for sandbox.cli — `sandbox job init`."""

import pytest
from pathlib import Path
from unittest.mock import patch, call

from sandbox.cli import _validate_job_name, _validate_table_names, create_job


class TestJobNameValidation:
    @pytest.mark.parametrize("name", [
        "my_job", "2026_06_10_backfill", "a", "abc123", "a" * 128,
    ])
    def test_valid_names(self, name):
        assert _validate_job_name(name) is None

    def test_rejects_sandbox_prefix(self):
        assert _validate_job_name("sandbox_something") is not None

    def test_rejects_uppercase(self):
        assert _validate_job_name("MyJob") is not None

    def test_rejects_hyphen(self):
        assert _validate_job_name("my-job") is not None

    def test_rejects_empty(self):
        assert _validate_job_name("") is not None

    def test_rejects_too_long(self):
        assert _validate_job_name("a" * 129) is not None

    def test_allows_digit_start(self):
        assert _validate_job_name("2026_backfill") is None


class TestTableNamesValidation:
    def test_valid_single(self):
        ok, err = _validate_table_names("orders")
        assert ok == ["orders"]
        assert err is None

    def test_valid_multiple(self):
        ok, err = _validate_table_names("orders, customers")
        assert ok == ["orders", "customers"]
        assert err is None

    def test_rejects_invalid(self):
        ok, err = _validate_table_names("BadName")
        assert ok is None
        assert err is not None

    def test_rejects_empty(self):
        ok, err = _validate_table_names("")
        assert ok is None
        assert err is not None


class TestCreateJob:
    def test_creates_daily_job_file(self, tmp_path):
        daily_dir = tmp_path / "daily"
        daily_dir.mkdir()
        one_time_dir = tmp_path / "one_time"
        one_time_dir.mkdir()

        path = create_job(
            jobs_root=tmp_path,
            job_name="customer_summary",
            job_type="daily",
            owner="analytics",
            output_tables=["customer_summary"],
            description="Daily customer summary table.",
        )

        assert path == daily_dir / "customer_summary.py"
        assert path.exists()
        content = path.read_text()
        assert '"""Daily customer summary table."""' in content
        assert 'OWNER = "analytics"' in content
        assert 'OUTPUT_TABLES = ["customer_summary"]' in content
        assert "def main()" in content

    def test_creates_one_time_job_file(self, tmp_path):
        (tmp_path / "daily").mkdir()
        one_time_dir = tmp_path / "one_time"
        one_time_dir.mkdir()

        path = create_job(
            jobs_root=tmp_path,
            job_name="2026_06_10_backfill",
            job_type="one_time",
            owner="data_eng",
            output_tables=["backfill_orders"],
            description="One-time backfill of orders.",
        )

        assert path == one_time_dir / "2026_06_10_backfill.py"

    def test_refuses_to_overwrite_existing(self, tmp_path):
        (tmp_path / "daily").mkdir()
        (tmp_path / "one_time").mkdir()
        existing = tmp_path / "daily" / "my_job.py"
        existing.write_text("# existing")

        with pytest.raises(FileExistsError):
            create_job(
                jobs_root=tmp_path,
                job_name="my_job",
                job_type="daily",
                owner="analytics",
                output_tables=["t"],
                description="desc",
            )

    def test_multiple_output_tables(self, tmp_path):
        (tmp_path / "daily").mkdir()
        (tmp_path / "one_time").mkdir()

        path = create_job(
            jobs_root=tmp_path,
            job_name="multi",
            job_type="daily",
            owner="analytics",
            output_tables=["table_a", "table_b"],
            description="Multi-output job.",
        )
        content = path.read_text()
        assert '"table_a"' in content
        assert '"table_b"' in content


class TestCLIInteraction:
    def test_main_prompts_and_creates_file(self, tmp_path, monkeypatch, capsys):
        jobs_root = tmp_path
        (jobs_root / "daily").mkdir()
        (jobs_root / "one_time").mkdir()

        monkeypatch.setattr("sandbox.cli.JOBS_ROOT", jobs_root)

        inputs = iter([
            "my_job",        # job name
            "daily",         # job type
            "analytics",     # owner
            "my_table",      # output tables
            "A daily job.",  # description
        ])
        with patch("builtins.input", lambda prompt="": next(inputs)):
            from sandbox.cli import cmd_init
            cmd_init()

        out = capsys.readouterr().out
        assert "my_job.py" in out
        assert (jobs_root / "daily" / "my_job.py").exists()
