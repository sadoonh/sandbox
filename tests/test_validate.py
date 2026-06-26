"""Tests for sandbox.validate."""

import shutil
from pathlib import Path

import pytest

from sandbox.validate import validate, ValidationError

FIXTURES = Path(__file__).parent / "fixtures"


def _make_job_tree(tmp_path: Path, daily: list = (), one_time: list = ()) -> Path:
    """Copy fixture files into a temporary jobs directory tree."""
    (tmp_path / "daily").mkdir()
    (tmp_path / "one_time").mkdir()
    for fixture_name, dest_name in daily:
        src = FIXTURES / fixture_name
        shutil.copy(src, tmp_path / "daily" / dest_name)
    for fixture_name, dest_name in one_time:
        src = FIXTURES / fixture_name
        shutil.copy(src, tmp_path / "one_time" / dest_name)
    return tmp_path


class TestValidatePassingJob:
    def test_valid_daily_job_passes(self, tmp_path):
        root = _make_job_tree(tmp_path, daily=[("valid_daily_job.py", "customer_summary.py")])
        errors = validate(root)
        assert errors == []

    def test_empty_jobs_dir_passes(self, tmp_path):
        _make_job_tree(tmp_path)
        assert validate(tmp_path) == []

    def test_init_py_is_ignored(self, tmp_path):
        root = _make_job_tree(tmp_path)
        (root / "daily" / "__init__.py").write_text("")
        assert validate(root) == []


class TestValidateFailingJobs:
    def test_missing_docstring(self, tmp_path):
        root = _make_job_tree(tmp_path, daily=[("job_missing_docstring.py", "job_missing_docstring.py")])
        errors = validate(root)
        assert any("docstring" in e.lower() for e in errors)

    def test_missing_owner(self, tmp_path):
        root = _make_job_tree(tmp_path, daily=[("job_missing_owner.py", "job_missing_owner.py")])
        errors = validate(root)
        assert any("OWNER" in e for e in errors)

    def test_empty_owner(self, tmp_path):
        root = _make_job_tree(tmp_path, daily=[("job_empty_owner.py", "job_empty_owner.py")])
        errors = validate(root)
        assert any("OWNER" in e for e in errors)

    def test_bad_table_name(self, tmp_path):
        root = _make_job_tree(tmp_path, daily=[("job_bad_table_name.py", "job_bad_table_name.py")])
        errors = validate(root)
        assert any("OUTPUT_TABLES" in e or "table" in e.lower() for e in errors)

    def test_empty_output_tables(self, tmp_path):
        root = _make_job_tree(tmp_path, daily=[("job_empty_output_tables.py", "job_empty_output_tables.py")])
        errors = validate(root)
        assert any("OUTPUT_TABLES" in e for e in errors)

    def test_missing_main(self, tmp_path):
        root = _make_job_tree(tmp_path, daily=[("job_missing_main.py", "job_missing_main.py")])
        errors = validate(root)
        assert any("main" in e.lower() for e in errors)

    def test_async_main(self, tmp_path):
        root = _make_job_tree(tmp_path, daily=[("job_async_main.py", "job_async_main.py")])
        errors = validate(root)
        assert any("async" in e.lower() for e in errors)

    def test_main_with_args(self, tmp_path):
        root = _make_job_tree(tmp_path, daily=[("job_main_with_args.py", "job_main_with_args.py")])
        errors = validate(root)
        assert any("parameter" in e.lower() or "argument" in e.lower() for e in errors)

    def test_import_time_io_blocked(self, tmp_path):
        root = _make_job_tree(tmp_path, daily=[("job_import_time_io.py", "job_import_time_io.py")])
        errors = validate(root)
        assert any("import time" in e.lower() or "main()" in e for e in errors)

    def test_invalid_filename_sandbox_prefix(self, tmp_path):
        root = _make_job_tree(tmp_path)
        (root / "daily" / "sandbox_bad.py").write_text(
            '"""desc"""\nOWNER = "x"\nOUTPUT_TABLES = ["t"]\ndef main(): pass\n'
        )
        errors = validate(root)
        assert any("sandbox_" in e for e in errors)


class TestValidateDuplicateOutputTables:
    def test_duplicate_output_table_across_jobs_fails(self, tmp_path):
        root = _make_job_tree(tmp_path)
        (root / "daily" / "job_a.py").write_text(
            '"""Job A."""\nOWNER = "x"\nOUTPUT_TABLES = ["shared_table"]\ndef main(): pass\n'
        )
        (root / "daily" / "job_b.py").write_text(
            '"""Job B."""\nOWNER = "y"\nOUTPUT_TABLES = ["shared_table"]\ndef main(): pass\n'
        )
        errors = validate(root)
        assert any("shared_table" in e for e in errors)

    def test_unique_output_tables_pass(self, tmp_path):
        root = _make_job_tree(tmp_path)
        (root / "daily" / "job_a.py").write_text(
            '"""Job A."""\nOWNER = "x"\nOUTPUT_TABLES = ["table_a"]\ndef main(): pass\n'
        )
        (root / "daily" / "job_b.py").write_text(
            '"""Job B."""\nOWNER = "y"\nOUTPUT_TABLES = ["table_b"]\ndef main(): pass\n'
        )
        assert validate(root) == []


class TestValidateExitCode:
    def test_raises_validation_error_on_failures(self, tmp_path):
        root = _make_job_tree(tmp_path, daily=[("job_missing_docstring.py", "job_missing_docstring.py")])
        with pytest.raises(ValidationError):
            validate(root, raise_on_failure=True)

    def test_no_exception_on_success(self, tmp_path):
        root = _make_job_tree(tmp_path, daily=[("valid_daily_job.py", "customer_summary.py")])
        validate(root, raise_on_failure=True)  # should not raise
