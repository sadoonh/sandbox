"""Browser-based job creation wizard."""

from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from sandbox.job_creation import create_job, validate_job_name, validate_table_names

PROJECT_ROOT = Path(__file__).resolve().parent
JOBS_ROOT = PROJECT_ROOT / "sandbox" / "jobs"
_JOB_TYPES = {
    "Daily — scheduled every day at 09:00 UTC": "daily",
    "One-time — runs once after merge to main": "one_time",
}


@dataclass(frozen=True)
class JobDraft:
    name: str
    job_type: str
    owner: str
    output_tables: list[str]
    description: str


def validate_draft(
    name: str,
    job_type: str,
    owner: str,
    tables_raw: str,
    description: str,
) -> tuple[JobDraft | None, list[str]]:
    """Validate form values using the same rules as the terminal wizard."""
    name = name.strip()
    owner = owner.strip()
    description = description.strip()
    errors: list[str] = []

    if error := validate_job_name(name):
        errors.append(error)
    if job_type not in _JOB_TYPES.values():
        errors.append("Choose a valid job type.")
    if not owner:
        errors.append("Author / owner is required.")

    output_tables, tables_error = validate_table_names(tables_raw)
    if tables_error:
        errors.append(tables_error)
    if not description:
        errors.append("Description is required.")

    if errors:
        return None, errors

    assert output_tables is not None
    return JobDraft(name, job_type, owner, output_tables, description), []


def _job_form() -> None:
    st.subheader("Create a transformation job")
    st.caption("Fill in the details below. No command line needed.")

    with st.form("job_details"):
        name = st.text_input(
            "Job name",
            help="Use lowercase letters, numbers, and underscores.",
            placeholder="customer_summary",
        )
        type_label = st.radio("Job type", list(_JOB_TYPES), horizontal=True)
        owner = st.text_input(
            "Author / owner",
            help="Team or person responsible for this job.",
            placeholder="analytics",
        )
        tables_raw = st.text_input(
            "Output tables",
            help="Separate multiple table names with commas.",
            placeholder="customer_summary, customer_totals",
        )
        description = st.text_input(
            "Description",
            help="A short, one-line description of what the job does.",
            placeholder="Daily customer summary.",
        )
        submitted = st.form_submit_button("Review job", type="primary")

    if not submitted:
        return

    draft, errors = validate_draft(
        name, _JOB_TYPES[type_label], owner, tables_raw, description
    )
    if errors:
        for error in errors:
            st.error(error)
        return

    st.session_state.job_draft = draft
    st.rerun()


def _review(draft: JobDraft) -> None:
    destination = JOBS_ROOT / draft.job_type / f"{draft.name}.py"
    display_location = destination.relative_to(PROJECT_ROOT)

    st.subheader("Review")
    st.write("Check these details before creating the job file.")
    st.table(
        {
            "Field": ["Job", "Type", "Author", "Tables", "Description", "Location"],
            "Value": [
                draft.name,
                draft.job_type,
                draft.owner,
                ", ".join(draft.output_tables),
                draft.description,
                str(display_location),
            ],
        }
    )

    create_column, back_column = st.columns(2)
    if create_column.button("Create job", type="primary", use_container_width=True):
        try:
            created = create_job(
                jobs_root=JOBS_ROOT,
                job_name=draft.name,
                job_type=draft.job_type,
                owner=draft.owner,
                output_tables=draft.output_tables,
                description=draft.description,
            )
        except (OSError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state.created_job = str(created.relative_to(PROJECT_ROOT))
            del st.session_state.job_draft
            st.rerun()

    if back_column.button("Back", use_container_width=True):
        del st.session_state.job_draft
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Sandbox Job Wizard", page_icon="🧰")
    st.title("Sandbox Job Wizard")

    if created := st.session_state.pop("created_job", None):
        st.success(f"Created {created}")
        st.info("Next: open the file and fill in main().")

    draft = st.session_state.get("job_draft")
    if draft is None:
        _job_form()
    else:
        _review(draft)


if __name__ == "__main__":
    main()
