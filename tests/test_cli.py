"""Tests for the sandbox CLI."""

import os
import sys

import pytest
from pathlib import Path
from unittest.mock import patch, call

from sandbox.cli import (
    _build_parser,
    _validate_job_name,
    _validate_table_names,
    cmd_list_jobs,
    cmd_run,
    create_job,
    main,
)


class TestCompactHelp:
    @pytest.mark.parametrize(
        ("args", "usage"),
        [
            (["--help"], "Usage: sandbox {job,list,validate}"),
            (["job", "--help"], "Usage: sandbox job {init,run}"),
            (["job", "init", "--help"], "Usage: sandbox job init"),
            (
                ["job", "run", "--help"],
                "Usage: sandbox job run JOB_ID [--dry-run] [--run-date YYYY-MM-DD]",
            ),
            (["list", "--help"], "Usage: sandbox list {job}"),
            (["list", "job", "--help"], "Usage: sandbox list job"),
            (["validate", "--help"], "Usage: sandbox validate"),
        ],
    )
    def test_help_is_minimal_and_consistent(self, args, usage, capsys):
        with pytest.raises(SystemExit) as exit_info:
            _build_parser().parse_args(args)

        assert exit_info.value.code == 0
        output = capsys.readouterr().out
        assert output.startswith(f"{usage}\n")
        assert "usage:" not in output
        assert "positional arguments:" not in output
        assert "show this help message and exit" not in output
        assert "Sandbox job framework CLI" not in output

    def test_root_help_lists_commands_before_options(self, capsys):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])

        output = capsys.readouterr().out
        assert "Usage: sandbox {job,list,validate}" in output
        commands = output.split("Commands:\n", 1)[1].split("\n\nOptions:", 1)[0]
        assert "{job,list,validate}" not in commands
        assert output.index("Commands:") < output.index("Options:")


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


JOB_STUB = '''\
"""Stub job."""
OWNER = "analytics"
OUTPUT_TABLES = ["t"]

def main():
    pass
'''


def _make_jobs_root(tmp_path: Path) -> Path:
    (tmp_path / "daily").mkdir()
    (tmp_path / "one_time").mkdir()
    return tmp_path


class TestCmdListJobs:
    def test_lists_jobs_with_metadata(self, tmp_path, capsys):
        root = _make_jobs_root(tmp_path)
        (root / "daily" / "daily_summary.py").write_text(
            JOB_STUB.replace('OWNER = "analytics"', 'OWNER = "data_team"')
        )
        (root / "one_time" / "backfill.py").write_text(JOB_STUB)

        cmd_list_jobs(root)

        output = capsys.readouterr().out
        assert output.startswith("╭")
        assert output.rstrip().endswith("╯")
        assert "JOB" in output
        assert "TYPE" in output
        assert "AUTHOR" in output
        assert "LOCATION" in output
        assert "RUNS" in output
        assert "daily_summary" in output
        assert "daily" in output
        assert "data_team" in output
        assert "Daily 09:00 UTC" in output
        assert "daily/daily_summary.py" in output
        assert "backfill" in output
        assert "one_time" in output
        assert "After merge (once)" in output

    def test_does_not_execute_job_while_reading_metadata(self, tmp_path, capsys):
        root = _make_jobs_root(tmp_path)
        (root / "daily" / "unsafe.py").write_text(
            '"""Unsafe job."""\nOWNER = "platform"\nraise RuntimeError("do not run")\n'
        )

        cmd_list_jobs(root)

        output = capsys.readouterr().out
        assert "unsafe" in output
        assert "platform" in output

    def test_shows_unknown_for_unreadable_owner(self, tmp_path, capsys):
        root = _make_jobs_root(tmp_path)
        (root / "daily" / "broken.py").write_text("not valid Python !!!")

        cmd_list_jobs(root)

        assert "<unknown>" in capsys.readouterr().out

    def test_reports_when_no_jobs_exist(self, tmp_path, capsys):
        root = _make_jobs_root(tmp_path)

        cmd_list_jobs(root)

        assert capsys.readouterr().out == "No sandbox jobs found.\n"

    def test_list_job_cli_dispatches(self, tmp_path, monkeypatch, capsys):
        root = _make_jobs_root(tmp_path)
        (root / "daily" / "my_job.py").write_text(JOB_STUB)
        monkeypatch.setattr("sandbox.cli.JOBS_ROOT", root)
        monkeypatch.setattr(sys, "argv", ["sandbox", "list", "job"])

        main()

        assert "my_job" in capsys.readouterr().out


class TestCmdRun:
    def test_unknown_job_id_fails(self, tmp_path, capsys):
        root = _make_jobs_root(tmp_path)
        assert cmd_run("nope", jobs_root=root) is False
        assert "no job found" in capsys.readouterr().err

    def test_ambiguous_job_id_fails(self, tmp_path, capsys):
        root = _make_jobs_root(tmp_path)
        (root / "daily" / "dupe.py").write_text(JOB_STUB)
        (root / "one_time" / "dupe.py").write_text(JOB_STUB)
        assert cmd_run("dupe", jobs_root=root) is False
        assert "both daily/ and one_time/" in capsys.readouterr().err

    def test_dispatches_to_runner_with_resolved_type(self, tmp_path):
        root = _make_jobs_root(tmp_path)
        (root / "one_time" / "my_backfill.py").write_text(JOB_STUB)
        with patch("sandbox.runner.run", return_value=True) as mock_run:
            assert cmd_run("my_backfill", jobs_root=root) is True
        mock_run.assert_called_once_with("one_time", job_id="my_backfill", jobs_root=root)

    def test_invalid_run_date_fails_before_running(self, tmp_path, capsys):
        root = _make_jobs_root(tmp_path)
        (root / "daily" / "my_job.py").write_text(JOB_STUB)
        with patch("sandbox.runner.run") as mock_run:
            assert cmd_run("my_job", run_date="not-a-date", jobs_root=root) is False
        mock_run.assert_not_called()
        assert "YYYY-MM-DD" in capsys.readouterr().err

    def test_dry_run_sets_env(self, tmp_path, monkeypatch):
        root = _make_jobs_root(tmp_path)
        (root / "daily" / "my_job.py").write_text(JOB_STUB)
        monkeypatch.setenv("SANDBOX_DRY_RUN", "false")  # registers teardown restore
        with patch("sandbox.runner.run", return_value=True):
            cmd_run("my_job", dry_run=True, jobs_root=root)
        assert os.environ["SANDBOX_DRY_RUN"] == "true"

    def test_run_date_sets_env(self, tmp_path, monkeypatch):
        root = _make_jobs_root(tmp_path)
        (root / "daily" / "my_job.py").write_text(JOB_STUB)
        monkeypatch.setenv("SANDBOX_RUN_DATE", "1970-01-01")  # registers teardown restore
        with patch("sandbox.runner.run", return_value=True):
            cmd_run("my_job", run_date="2026-07-01", jobs_root=root)
        assert os.environ["SANDBOX_RUN_DATE"] == "2026-07-01"


class TestCLIInteraction:
    def test_wizard_creates_file(self, tmp_path, monkeypatch, capsys):
        jobs_root = tmp_path
        (jobs_root / "daily").mkdir()
        (jobs_root / "one_time").mkdir()

        monkeypatch.setattr("sandbox.cli.JOBS_ROOT", jobs_root)
        text_answers = iter(["my_job", "analytics", "my_table", "A daily job."])
        monkeypatch.setattr(
            "sandbox.job_wizard._ask_text",
            lambda message, validate: next(text_answers),
        )
        monkeypatch.setattr(
            "sandbox.job_wizard._ask_select",
            lambda message, choices: "daily",
        )
        monkeypatch.setattr("sandbox.job_wizard._ask_confirm", lambda message: True)

        from sandbox.cli import cmd_init

        assert cmd_init() is True
        out = capsys.readouterr().out
        assert "SANDBOX" in out
        assert "Review" in out
        assert "Success" in out
        assert "my_job.py" in out
        assert (jobs_root / "daily" / "my_job.py").exists()
