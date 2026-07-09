"""Tests for sandbox._helpers (run_date, dry_run, load_env_file)."""

import datetime
import os

import pytest
from sandbox._helpers import load_env_file, run_date, dry_run
from sandbox.exceptions import SandboxConfigError


class TestRunDate:
    def test_returns_today_utc_when_not_set(self, monkeypatch):
        monkeypatch.delenv("SANDBOX_RUN_DATE", raising=False)
        result = run_date()
        assert result == datetime.datetime.now(datetime.timezone.utc).date()

    def test_parses_valid_date(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_RUN_DATE", "2026-01-15")
        assert run_date() == datetime.date(2026, 1, 15)

    def test_raises_on_invalid_format(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_RUN_DATE", "01-15-2026")
        with pytest.raises(SandboxConfigError, match="SANDBOX_RUN_DATE"):
            run_date()

    def test_raises_on_garbage(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_RUN_DATE", "not-a-date")
        with pytest.raises(SandboxConfigError, match="YYYY-MM-DD"):
            run_date()

    def test_raises_on_invalid_calendar_date(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_RUN_DATE", "2026-13-01")
        with pytest.raises(SandboxConfigError):
            run_date()


class TestDryRun:
    def test_false_when_not_set(self, monkeypatch):
        monkeypatch.delenv("SANDBOX_DRY_RUN", raising=False)
        assert dry_run() is False

    def test_false_for_empty_string(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_DRY_RUN", "")
        assert dry_run() is False

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "YES"])
    def test_true_values(self, monkeypatch, value):
        monkeypatch.setenv("SANDBOX_DRY_RUN", value)
        assert dry_run() is True

    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no", "NO"])
    def test_false_values(self, monkeypatch, value):
        monkeypatch.setenv("SANDBOX_DRY_RUN", value)
        assert dry_run() is False

    def test_raises_on_invalid_value(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_DRY_RUN", "maybe")
        with pytest.raises(SandboxConfigError, match="SANDBOX_DRY_RUN"):
            dry_run()


def _unset_env(monkeypatch, name):
    # setenv first so monkeypatch registers a teardown that also undoes
    # any value load_env_file() writes directly into os.environ.
    monkeypatch.setenv(name, "placeholder")
    monkeypatch.delenv(name)


class TestLoadEnvFile:
    def test_loads_values_from_dotenv(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SANDBOX_BUCKET=from-file\n")
        monkeypatch.chdir(tmp_path)
        _unset_env(monkeypatch, "SANDBOX_BUCKET")
        load_env_file()
        assert os.environ["SANDBOX_BUCKET"] == "from-file"

    def test_existing_env_vars_win_over_file(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SANDBOX_BUCKET=from-file\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SANDBOX_BUCKET", "from-shell")
        load_env_file()
        assert os.environ["SANDBOX_BUCKET"] == "from-shell"

    def test_finds_dotenv_in_parent_directory(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SANDBOX_DATABASE=parent-db\n")
        subdir = tmp_path / "sub"
        subdir.mkdir()
        monkeypatch.chdir(subdir)
        _unset_env(monkeypatch, "SANDBOX_DATABASE")
        load_env_file()
        assert os.environ["SANDBOX_DATABASE"] == "parent-db"

    def test_missing_dotenv_is_a_noop(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SANDBOX_BUCKET", raising=False)
        load_env_file()
        assert "SANDBOX_BUCKET" not in os.environ
