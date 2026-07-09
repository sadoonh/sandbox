"""Tests for sandbox.runner."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pandas as pd
import pytest

from sandbox import runner


FIXTURES = Path(__file__).parent / "fixtures"


def _write_job(directory: Path, filename: str, content: str) -> Path:
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


def _make_jobs_root(tmp_path: Path) -> Path:
    (tmp_path / "daily").mkdir()
    (tmp_path / "one_time").mkdir()
    return tmp_path


GOOD_DAILY_JOB = '''\
"""Good daily job."""
OWNER = "analytics"
OUTPUT_TABLES = ["summary"]

def main():
    pass
'''

FAILING_JOB = '''\
"""Failing job."""
OWNER = "analytics"
OUTPUT_TABLES = ["summary"]

def main():
    raise RuntimeError("boom")
'''

WRITE_TABLE_JOB = '''\
"""Write table job."""
OWNER = "analytics"
OUTPUT_TABLES = ["my_output"]

def main():
    import pandas as pd
    from sandbox import io
    io.write_table(pd.DataFrame({"a": [1, 2]}), "my_output")
'''

UNDECLARED_WRITE_JOB = '''\
"""Undeclared write job."""
OWNER = "analytics"
OUTPUT_TABLES = ["declared"]

def main():
    import pandas as pd
    from sandbox import io
    io.write_table(pd.DataFrame({"a": [1]}), "not_declared")
'''

BAD_IMPORT_JOB = '''\
"""Bad import job."""
import this_module_does_not_exist_xyz
OWNER = "analytics"
OUTPUT_TABLES = ["t"]

def main():
    pass
'''


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

class TestDiscoverJobs:
    def test_discovers_daily_jobs(self, tmp_path):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "daily", "job_a.py", GOOD_DAILY_JOB)
        _write_job(root / "daily", "job_b.py", GOOD_DAILY_JOB)
        (root / "daily" / "__init__.py").write_text("")
        paths = runner._discover_jobs("daily", root)
        names = [p.stem for p in paths]
        assert "job_a" in names
        assert "job_b" in names
        assert "__init__" not in names

    def test_ignores_init_py(self, tmp_path):
        root = _make_jobs_root(tmp_path)
        (root / "daily" / "__init__.py").write_text("")
        assert runner._discover_jobs("daily", root) == []

    def test_job_id_filter(self, tmp_path):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "daily", "job_a.py", GOOD_DAILY_JOB)
        _write_job(root / "daily", "job_b.py", GOOD_DAILY_JOB)
        paths = runner._discover_jobs("daily", root, job_id="job_a")
        assert len(paths) == 1
        assert paths[0].stem == "job_a"


# ---------------------------------------------------------------------------
# One-time state
# ---------------------------------------------------------------------------

class TestOneTimeState:
    def test_missing_file_treated_as_empty(self, sandbox_aws):
        data = runner._load_completed_jobs("test-bucket")
        assert data == {}

    def test_saves_and_loads(self, sandbox_aws):
        data = {"my_job": {"timestamp": "2026-01-01T00:00:00Z", "run_id": "abc", "commit_sha": "def"}}
        runner._save_completed_jobs("test-bucket", data)
        loaded = runner._load_completed_jobs("test-bucket")
        assert loaded == data

    def test_corrupt_state_file_raises(self, sandbox_aws):
        import boto3
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.put_object(
            Bucket="test-bucket",
            Key="sandbox-platform/state/completed_jobs.json",
            Body=b"not json",
        )
        with pytest.raises(Exception, match="state"):
            runner._load_completed_jobs("test-bucket")


# ---------------------------------------------------------------------------
# Full run scenarios (mocked log writing)
# ---------------------------------------------------------------------------

class TestRunDaily:
    def test_successful_daily_job(self, tmp_path, sandbox_env, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "daily", "good_job.py", GOOD_DAILY_JOB)
        mock_log = mocker.patch("sandbox.runner._try_write_log")

        succeeded = runner.run("daily", jobs_root=root)

        assert succeeded is True
        mock_log.assert_called_once()
        record = mock_log.call_args[0][2]
        assert record["job_id"] == "good_job"
        assert record["status"] == "success"
        assert record["owner"] == "analytics"
        assert record["job_type"] == "daily"
        assert record["error_message"] is None

    def test_failing_daily_job_returns_false(self, tmp_path, sandbox_env, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "daily", "bad_job.py", FAILING_JOB)
        mocker.patch("sandbox.runner._try_write_log")

        succeeded = runner.run("daily", jobs_root=root)

        assert succeeded is False

    def test_failing_job_log_has_error(self, tmp_path, sandbox_env, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "daily", "bad_job.py", FAILING_JOB)
        mock_log = mocker.patch("sandbox.runner._try_write_log")

        runner.run("daily", jobs_root=root)

        record = mock_log.call_args[0][2]
        assert record["status"] == "failed"
        assert "boom" in record["error_message"]

    def test_one_failure_does_not_stop_other_jobs(self, tmp_path, sandbox_env, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "daily", "job_a.py", FAILING_JOB)
        _write_job(root / "daily", "job_b.py", GOOD_DAILY_JOB)
        mock_log = mocker.patch("sandbox.runner._try_write_log")

        runner.run("daily", jobs_root=root)

        assert mock_log.call_count == 2
        statuses = {c[0][2]["job_id"]: c[0][2]["status"] for c in mock_log.call_args_list}
        assert statuses["job_a"] == "failed"
        assert statuses["job_b"] == "success"

    def test_import_failure_logged_as_failed(self, tmp_path, sandbox_env, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "daily", "bad_import.py", BAD_IMPORT_JOB)
        mock_log = mocker.patch("sandbox.runner._try_write_log")

        runner.run("daily", jobs_root=root)

        record = mock_log.call_args[0][2]
        assert record["status"] == "failed"
        assert record["owner"] is None
        assert "import" in record["error_message"].lower()


class TestRunOneTime:
    def test_skips_already_completed_job(self, tmp_path, sandbox_env, sandbox_aws, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "one_time", "done_job.py", GOOD_DAILY_JOB)
        runner._save_completed_jobs("test-bucket", {
            "done_job": {"timestamp": "2026-01-01T00:00:00Z", "run_id": "x", "commit_sha": "y"}
        })
        mock_log = mocker.patch("sandbox.runner._try_write_log")

        runner.run("one_time", jobs_root=root)

        mock_log.assert_not_called()

    def test_runs_new_job_and_marks_complete(self, tmp_path, sandbox_env, sandbox_aws, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "one_time", "new_job.py", GOOD_DAILY_JOB)
        mocker.patch("sandbox.runner._try_write_log")

        runner.run("one_time", jobs_root=root)

        completed = runner._load_completed_jobs("test-bucket")
        assert "new_job" in completed
        assert "timestamp" in completed["new_job"]
        assert "run_id" in completed["new_job"]
        assert "commit_sha" in completed["new_job"]

    def test_failed_one_time_job_not_marked_complete(self, tmp_path, sandbox_env, sandbox_aws, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "one_time", "failing.py", FAILING_JOB)
        mocker.patch("sandbox.runner._try_write_log")

        runner.run("one_time", jobs_root=root)

        completed = runner._load_completed_jobs("test-bucket")
        assert "failing" not in completed

    def test_state_updated_after_each_success(self, tmp_path, sandbox_env, sandbox_aws, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "one_time", "job_a.py", GOOD_DAILY_JOB)
        _write_job(root / "one_time", "job_b.py", GOOD_DAILY_JOB)
        mocker.patch("sandbox.runner._try_write_log")

        save_calls = []
        original_save = runner._save_completed_jobs

        def tracking_save(bucket, data):
            save_calls.append(dict(data))
            original_save(bucket, data)

        mocker.patch("sandbox.runner._save_completed_jobs", side_effect=tracking_save)
        runner.run("one_time", jobs_root=root)

        # State saved after each job, not just at the end
        assert len(save_calls) == 2
        assert len(save_calls[0]) == 1  # first job only
        assert len(save_calls[1]) == 2  # both jobs

    def test_skips_already_completed_even_with_job_flag(self, tmp_path, sandbox_env, sandbox_aws, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "one_time", "done.py", GOOD_DAILY_JOB)
        runner._save_completed_jobs("test-bucket", {
            "done": {"timestamp": "2026-01-01T00:00:00Z", "run_id": "x", "commit_sha": "y"}
        })
        mock_log = mocker.patch("sandbox.runner._try_write_log")

        runner.run("one_time", job_id="done", jobs_root=root)

        mock_log.assert_not_called()


class TestWriteRecorderIntegration:
    def test_table_writes_in_log_record(self, tmp_path, sandbox_env, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "daily", "writer.py", WRITE_TABLE_JOB)
        mock_log = mocker.patch("sandbox.runner._try_write_log")
        mocker.patch("awswrangler.s3.to_parquet")

        runner.run("daily", jobs_root=root)

        record = mock_log.call_args[0][2]
        writes = json.loads(record["table_writes"])
        assert len(writes) == 1
        assert writes[0]["table_name"] == "my_output"
        assert writes[0]["row_count"] == 2

    def test_undeclared_write_fails_job(self, tmp_path, sandbox_env, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "daily", "bad_write.py", UNDECLARED_WRITE_JOB)
        mock_log = mocker.patch("sandbox.runner._try_write_log")
        mocker.patch("awswrangler.s3.to_parquet")

        runner.run("daily", jobs_root=root)

        record = mock_log.call_args[0][2]
        assert record["status"] == "failed"
        assert "OUTPUT_TABLES" in record["error_message"]


class TestDryRun:
    def test_dry_run_does_not_write_log(self, tmp_path, sandbox_env, sandbox_aws, monkeypatch, mocker):
        monkeypatch.setenv("SANDBOX_DRY_RUN", "true")
        root = _make_jobs_root(tmp_path)
        _write_job(root / "one_time", "new_job.py", GOOD_DAILY_JOB)
        mock_write = mocker.patch("sandbox.runner._write_log_record")

        runner.run("one_time", jobs_root=root)

        mock_write.assert_not_called()

    def test_dry_run_does_not_update_state(self, tmp_path, sandbox_env, sandbox_aws, monkeypatch, mocker):
        monkeypatch.setenv("SANDBOX_DRY_RUN", "true")
        root = _make_jobs_root(tmp_path)
        _write_job(root / "one_time", "new_job.py", GOOD_DAILY_JOB)
        mocker.patch("sandbox.runner._try_write_log")

        runner.run("one_time", jobs_root=root)

        completed = runner._load_completed_jobs("test-bucket")
        assert "new_job" not in completed


class TestLogRecord:
    def test_log_record_fields(self, tmp_path, sandbox_env, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "daily", "my_job.py", GOOD_DAILY_JOB)
        mock_log = mocker.patch("sandbox.runner._try_write_log")

        runner.run("daily", jobs_root=root)

        record = mock_log.call_args[0][2]
        required_fields = [
            "run_id", "job_id", "job_type", "owner", "status",
            "started_at", "finished_at", "duration_seconds", "run_date",
            "declared_output_tables", "table_writes",
            "commit_sha", "github_run_id", "github_actor", "error_message",
        ]
        for field in required_fields:
            assert field in record, f"Missing field: {field}"

    def test_duration_at_least_one_second(self, tmp_path, sandbox_env, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "daily", "my_job.py", GOOD_DAILY_JOB)
        mock_log = mocker.patch("sandbox.runner._try_write_log")

        runner.run("daily", jobs_root=root)

        record = mock_log.call_args[0][2]
        assert record["duration_seconds"] >= 1

    def test_log_failure_does_not_affect_job_status(self, tmp_path, sandbox_env, sandbox_aws, mocker):
        root = _make_jobs_root(tmp_path)
        _write_job(root / "one_time", "new_job.py", GOOD_DAILY_JOB)
        mocker.patch("sandbox.runner._write_log_record", side_effect=Exception("log error"))

        succeeded = runner.run("one_time", jobs_root=root)

        # Job should still be marked complete despite log failure
        completed = runner._load_completed_jobs("test-bucket")
        assert "new_job" in completed
        assert succeeded is True

    def test_nullable_columns_get_explicit_dtype(self, mocker):
        # owner and error_message are None on a successful run; without an
        # explicit Athena dtype, awswrangler's type inference fails on the
        # all-null object column and the log write errors out.
        from datetime import datetime, timezone

        mock_to_parquet = mocker.patch("awswrangler.s3.to_parquet")
        now = datetime.now(timezone.utc)
        record = runner._build_record(
            "rid", "my_job", "daily", None, "success", now, now, 1,
            "2026-07-09", [], [], "sha", "run_id", "actor", None,
        )

        runner._write_log_record("test-bucket", "test_sandbox_db", record)

        kwargs = mock_to_parquet.call_args.kwargs
        assert kwargs["dtype"]["owner"] == "string"
        assert kwargs["dtype"]["error_message"] == "string"
