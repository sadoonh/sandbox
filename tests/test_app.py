"""Tests for the browser-based job wizard."""

import pytest

from app import validate_draft


def test_validate_draft_returns_normalized_job():
    draft, errors = validate_draft(
        " customer_summary ",
        "daily",
        " analytics ",
        "customer_summary, customer_totals",
        " Daily customer summary. ",
    )

    assert errors == []
    assert draft is not None
    assert draft.name == "customer_summary"
    assert draft.job_type == "daily"
    assert draft.owner == "analytics"
    assert draft.output_tables == ["customer_summary", "customer_totals"]
    assert draft.description == "Daily customer summary."


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", "Bad-Name", "lowercase"),
        ("job_type", "weekly", "valid job type"),
        ("owner", " ", "Author / owner is required"),
        ("tables_raw", "BadTable", "uppercase"),
        ("description", " ", "Description is required"),
    ],
)
def test_validate_draft_rejects_invalid_fields(field, value, message):
    values = {
        "name": "customer_summary",
        "job_type": "daily",
        "owner": "analytics",
        "tables_raw": "customer_summary",
        "description": "Daily customer summary.",
    }
    values[field] = value

    draft, errors = validate_draft(**values)

    assert draft is None
    assert any(message in error for error in errors)
