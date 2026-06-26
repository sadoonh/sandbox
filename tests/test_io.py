"""Tests for sandbox.io — table name validation, data functions, write recorder, dry-run."""

import importlib

import pandas as pd
import pytest

from sandbox.exceptions import SandboxConfigError, SandboxError, SandboxValidationError
from sandbox import io


# ---------------------------------------------------------------------------
# Table name validation
# ---------------------------------------------------------------------------

class TestValidateTableName:
    @pytest.mark.parametrize("name", [
        "orders", "orders_clean", "a", "a1", "abc123", "a" * 128,
    ])
    def test_valid_names(self, name):
        io._validate_table_name(name)  # should not raise

    def test_rejects_empty(self):
        with pytest.raises(SandboxValidationError, match="empty"):
            io._validate_table_name("")

    def test_rejects_sandbox_prefix(self):
        with pytest.raises(SandboxValidationError, match="reserved"):
            io._validate_table_name("sandbox_job_runs")

    def test_rejects_uppercase(self):
        with pytest.raises(SandboxValidationError, match="uppercase"):
            io._validate_table_name("Orders")

    def test_rejects_starting_with_digit(self):
        with pytest.raises(SandboxValidationError, match="letter"):
            io._validate_table_name("1orders")

    def test_rejects_special_chars(self):
        with pytest.raises(SandboxValidationError, match="invalid"):
            io._validate_table_name("orders-clean")

    def test_rejects_too_long(self):
        with pytest.raises(SandboxValidationError, match="128"):
            io._validate_table_name("a" * 129)

    def test_rejects_hyphen(self):
        with pytest.raises(SandboxValidationError):
            io._validate_table_name("my-table")

    def test_rejects_space(self):
        with pytest.raises(SandboxValidationError):
            io._validate_table_name("my table")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

class TestConfig:
    def test_missing_bucket_raises_config_error(self, monkeypatch, sandbox_env, sandbox_aws):
        monkeypatch.delenv("SANDBOX_BUCKET")
        df = pd.DataFrame({"x": [1]})
        with pytest.raises(SandboxConfigError, match="SANDBOX_BUCKET"):
            io.write_table(df, "my_table")

    def test_bucket_with_s3_uri_raises(self, monkeypatch, sandbox_env):
        monkeypatch.setenv("SANDBOX_BUCKET", "s3://my-bucket")
        df = pd.DataFrame({"x": [1]})
        with pytest.raises(SandboxConfigError, match="s3://"):
            io.write_table(df, "my_table")

    def test_missing_database_raises_config_error(self, monkeypatch, sandbox_env, sandbox_aws):
        monkeypatch.delenv("SANDBOX_DATABASE")
        df = pd.DataFrame({"x": [1]})
        with pytest.raises(SandboxConfigError, match="SANDBOX_DATABASE"):
            io.write_table(df, "my_table")


# ---------------------------------------------------------------------------
# query()
# ---------------------------------------------------------------------------

class TestQuery:
    def test_calls_awswrangler(self, sandbox_env, mocker):
        mock_read = mocker.patch("awswrangler.athena.read_sql_query")
        mock_read.return_value = pd.DataFrame({"col": [1, 2]})
        result = io.query("SELECT 1")
        mock_read.assert_called_once()
        call_kwargs = mock_read.call_args
        assert call_kwargs[0][0] == "SELECT 1"
        assert len(result) == 2

    def test_passes_database(self, sandbox_env, mocker):
        mock_read = mocker.patch("awswrangler.athena.read_sql_query")
        mock_read.return_value = pd.DataFrame()
        io.query("SELECT 1")
        _, kwargs = mock_read.call_args
        assert kwargs.get("database") == "test_sandbox_db"

    def test_passes_workgroup_when_set(self, sandbox_env, monkeypatch, mocker):
        monkeypatch.setenv("SANDBOX_WORKGROUP", "sandbox-wg")
        mock_read = mocker.patch("awswrangler.athena.read_sql_query")
        mock_read.return_value = pd.DataFrame()
        io.query("SELECT 1")
        _, kwargs = mock_read.call_args
        assert kwargs.get("workgroup") == "sandbox-wg"

    def test_omits_workgroup_when_not_set(self, sandbox_env, monkeypatch, mocker):
        monkeypatch.delenv("SANDBOX_WORKGROUP", raising=False)
        mock_read = mocker.patch("awswrangler.athena.read_sql_query")
        mock_read.return_value = pd.DataFrame()
        io.query("SELECT 1")
        _, kwargs = mock_read.call_args
        assert "workgroup" not in kwargs


# ---------------------------------------------------------------------------
# read_table()
# ---------------------------------------------------------------------------

class TestReadTable:
    def test_delegates_to_query(self, sandbox_env, mocker):
        mock_query = mocker.patch("sandbox.io.query")
        mock_query.return_value = pd.DataFrame({"a": [1]})
        io.read_table("orders")
        mock_query.assert_called_once()
        sql = mock_query.call_args[0][0]
        assert "orders" in sql
        assert "test_sandbox_db" in sql

    def test_validates_table_name(self, sandbox_env):
        with pytest.raises(SandboxValidationError):
            io.read_table("Invalid-Name")


# ---------------------------------------------------------------------------
# write_table()
# ---------------------------------------------------------------------------

class TestWriteTable:
    def test_writes_parquet_to_s3(self, sandbox_env, sandbox_aws, mocker):
        mock_write = mocker.patch("awswrangler.s3.to_parquet")
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        io.write_table(df, "my_table")
        mock_write.assert_called_once()
        _, kwargs = mock_write.call_args
        assert kwargs["path"] == "s3://test-bucket/sandbox-tables/my_table/"
        assert kwargs["dataset"] is True
        assert kwargs["database"] == "test_sandbox_db"
        assert kwargs["table"] == "my_table"
        assert kwargs["mode"] == "overwrite"

    def test_default_if_exists_is_replace(self, sandbox_env, sandbox_aws, mocker):
        mock_write = mocker.patch("awswrangler.s3.to_parquet")
        io.write_table(pd.DataFrame({"a": [1]}), "t")
        _, kwargs = mock_write.call_args
        assert kwargs["mode"] == "overwrite"

    def test_append_mode(self, sandbox_env, sandbox_aws, mocker):
        mock_write = mocker.patch("awswrangler.s3.to_parquet")
        io.write_table(pd.DataFrame({"a": [1]}), "t", if_exists="append")
        _, kwargs = mock_write.call_args
        assert kwargs["mode"] == "append"

    def test_rejects_invalid_table_name(self, sandbox_env, sandbox_aws):
        with pytest.raises(SandboxValidationError):
            io.write_table(pd.DataFrame(), "BadName")

    def test_dry_run_skips_write(self, sandbox_env, monkeypatch, mocker):
        monkeypatch.setenv("SANDBOX_DRY_RUN", "true")
        mock_write = mocker.patch("awswrangler.s3.to_parquet")
        io.write_table(pd.DataFrame({"a": [1]}), "my_table")
        mock_write.assert_not_called()

    def test_records_write_when_recorder_active(self, sandbox_env, sandbox_aws, mocker):
        mocker.patch("awswrangler.s3.to_parquet")
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        writes = io._start_job_run(["my_table"])
        try:
            io.write_table(df, "my_table")
        finally:
            io._end_job_run()
        assert len(writes) == 1
        assert writes[0]["table_name"] == "my_table"
        assert writes[0]["row_count"] == 2
        assert writes[0]["column_count"] == 2
        assert writes[0]["columns"] == ["a", "b"]

    def test_blocks_undeclared_table_during_job_run(self, sandbox_env, sandbox_aws, mocker):
        mocker.patch("awswrangler.s3.to_parquet")
        io._start_job_run(["allowed_table"])
        try:
            with pytest.raises(SandboxValidationError, match="OUTPUT_TABLES"):
                io.write_table(pd.DataFrame({"a": [1]}), "other_table")
        finally:
            io._end_job_run()

    def test_dry_run_records_without_writing(self, sandbox_env, monkeypatch, mocker):
        monkeypatch.setenv("SANDBOX_DRY_RUN", "true")
        mock_write = mocker.patch("awswrangler.s3.to_parquet")
        df = pd.DataFrame({"a": [1]})
        writes = io._start_job_run(["my_table"])
        try:
            io.write_table(df, "my_table")
        finally:
            io._end_job_run()
        mock_write.assert_not_called()
        assert writes[0]["dry_run"] is True


# ---------------------------------------------------------------------------
# list_tables()
# ---------------------------------------------------------------------------

class TestListTables:
    def test_returns_user_tables(self, sandbox_env, mocker):
        mock_tables = mocker.patch("awswrangler.catalog.tables")
        import pandas as pd
        mock_tables.return_value = pd.DataFrame({
            "Table": ["orders", "customers", "sandbox_job_runs"],
            "Database": ["test_sandbox_db"] * 3,
        })
        result = io.list_tables()
        assert "orders" in result
        assert "customers" in result
        assert "sandbox_job_runs" not in result

    def test_returns_empty_list_when_no_tables(self, sandbox_env, mocker):
        mock_tables = mocker.patch("awswrangler.catalog.tables")
        mock_tables.return_value = pd.DataFrame({"Table": [], "Database": []})
        assert io.list_tables() == []


# ---------------------------------------------------------------------------
# delete_table()
# ---------------------------------------------------------------------------

class TestDeleteTable:
    def test_requires_confirm_true(self, sandbox_env):
        with pytest.raises(SandboxValidationError, match="confirm=True"):
            io.delete_table("my_table")

    def test_rejects_sandbox_prefix(self, sandbox_env):
        with pytest.raises(SandboxValidationError):
            io.delete_table("sandbox_job_runs", confirm=True)

    def test_blocked_during_job_run(self, sandbox_env):
        io._start_job_run(["some_table"])
        try:
            with pytest.raises(SandboxError, match="job run"):
                io.delete_table("my_table", confirm=True)
        finally:
            io._end_job_run()

    def test_rejects_table_outside_sandbox_location(self, sandbox_env, mocker):
        import boto3
        mock_glue = mocker.patch("boto3.client")
        mock_client = mock_glue.return_value
        mock_client.get_table.return_value = {
            "Table": {
                "StorageDescriptor": {
                    "Location": "s3://other-bucket/other-prefix/my_table/"
                }
            }
        }
        with pytest.raises(SandboxValidationError, match="sandbox table"):
            io.delete_table("my_table", confirm=True)

    def test_deletes_s3_and_glue(self, sandbox_env, mocker):
        mock_boto = mocker.patch("boto3.client")
        mock_client = mock_boto.return_value
        mock_client.get_table.return_value = {
            "Table": {
                "StorageDescriptor": {
                    "Location": "s3://test-bucket/sandbox-tables/my_table/"
                }
            }
        }
        mock_del = mocker.patch("awswrangler.s3.delete_objects")
        mock_drop = mocker.patch("awswrangler.catalog.delete_table_if_exists")
        io.delete_table("my_table", confirm=True)
        mock_del.assert_called_once_with(path="s3://test-bucket/sandbox-tables/my_table/")
        mock_drop.assert_called_once_with(database="test_sandbox_db", table="my_table")


# ---------------------------------------------------------------------------
# Validation mode (blocks IO at import time)
# ---------------------------------------------------------------------------

class TestValidationMode:
    def test_query_blocked_in_validation_mode(self, sandbox_env):
        with io._validation_context():
            with pytest.raises(SandboxError, match="import time"):
                io.query("SELECT 1")

    def test_write_table_blocked_in_validation_mode(self, sandbox_env):
        with io._validation_context():
            with pytest.raises(SandboxError, match="import time"):
                io.write_table(pd.DataFrame(), "t")

    def test_read_table_blocked_in_validation_mode(self, sandbox_env):
        with io._validation_context():
            with pytest.raises(SandboxError, match="import time"):
                io.read_table("t")

    def test_list_tables_blocked_in_validation_mode(self, sandbox_env):
        with io._validation_context():
            with pytest.raises(SandboxError, match="import time"):
                io.list_tables()

    def test_delete_table_blocked_in_validation_mode(self, sandbox_env):
        with io._validation_context():
            with pytest.raises(SandboxError, match="import time"):
                io.delete_table("t", confirm=True)

    def test_validation_mode_restored_after_exit(self, sandbox_env, mocker):
        mock_read = mocker.patch("awswrangler.athena.read_sql_query")
        mock_read.return_value = pd.DataFrame()
        with io._validation_context():
            pass
        io.query("SELECT 1")  # should not raise
