"""Tests for the sequential job-creation wizard."""

from io import StringIO

from rich.console import Console

from sandbox.job_wizard import JobDraft, run_job_init


def _jobs_root(tmp_path):
    (tmp_path / "daily").mkdir()
    (tmp_path / "one_time").mkdir()
    return tmp_path


def _recording_console():
    output = StringIO()
    return Console(file=output, color_system=None, width=100), output


def _draft():
    return JobDraft(
        name="customer_summary",
        job_type="daily",
        owner="analytics",
        output_tables=["customer_summary"],
        description="Daily customer summary.",
    )


def test_review_and_success_panels(tmp_path, monkeypatch):
    root = _jobs_root(tmp_path)
    console, output = _recording_console()
    monkeypatch.setattr("sandbox.job_wizard._collect_job_draft", lambda console: _draft())
    monkeypatch.setattr("sandbox.job_wizard._ask_confirm", lambda message: True)

    assert run_job_init(root, console=console) is True

    rendered = output.getvalue()
    assert "SANDBOX" in rendered
    assert "Review" in rendered
    assert "customer_summary" in rendered
    assert "analytics" in rendered
    assert "Success" in rendered
    assert (root / "daily" / "customer_summary.py").exists()


def test_declining_review_shows_neutral_cancellation(tmp_path, monkeypatch):
    root = _jobs_root(tmp_path)
    console, output = _recording_console()
    monkeypatch.setattr("sandbox.job_wizard._collect_job_draft", lambda console: _draft())
    monkeypatch.setattr("sandbox.job_wizard._ask_confirm", lambda message: False)

    assert run_job_init(root, console=console) is False

    rendered = output.getvalue()
    assert "Cancelled" in rendered
    assert "No files were changed" in rendered
    assert not (root / "daily" / "customer_summary.py").exists()


def test_keyboard_interrupt_shows_cancellation(tmp_path, monkeypatch):
    root = _jobs_root(tmp_path)
    console, output = _recording_console()

    def cancel(console):
        raise KeyboardInterrupt

    monkeypatch.setattr("sandbox.job_wizard._collect_job_draft", cancel)

    assert run_job_init(root, console=console) is False
    assert "Cancelled" in output.getvalue()


def test_existing_job_shows_error_panel(tmp_path, monkeypatch):
    root = _jobs_root(tmp_path)
    destination = root / "daily" / "customer_summary.py"
    destination.write_text("# existing")
    console, output = _recording_console()
    monkeypatch.setattr("sandbox.job_wizard._collect_job_draft", lambda console: _draft())
    monkeypatch.setattr("sandbox.job_wizard._ask_confirm", lambda message: True)

    assert run_job_init(root, console=console) is False

    rendered = output.getvalue()
    assert "Error" in rendered
    assert "already exists" in rendered
    assert destination.read_text() == "# existing"
