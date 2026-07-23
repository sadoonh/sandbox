"""Sequential terminal wizard for creating sandbox jobs."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import questionary
from questionary import Choice, Style, ValidationError, Validator
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from sandbox.job_creation import create_job, validate_job_name, validate_table_names

_TOTAL_STEPS = 5
_PROMPT_STYLE = Style(
    [
        ("qmark", "fg:#00bcd4 bold"),
        ("question", "bold"),
        ("answer", "fg:#00bcd4 bold"),
        ("pointer", "fg:#00bcd4 bold"),
        ("highlighted", "fg:#00bcd4 bold"),
        ("selected", "fg:#00bcd4"),
        ("checkbox", "fg:#00bcd4"),
        ("instruction", "fg:#777777"),
        ("separator", "fg:#777777"),
        ("disabled", "fg:#777777 italic"),
    ]
)


@dataclass(frozen=True)
class JobDraft:
    name: str
    job_type: str
    owner: str
    output_tables: list[str]
    description: str


class _TextValidator(Validator):
    def __init__(self, validate: Callable[[str], str | None]) -> None:
        self._validate = validate

    def validate(self, document: Any) -> None:
        error = self._validate(document.text)
        if error:
            raise ValidationError(
                message=error,
                cursor_position=len(document.text),
            )


def _ask_text(message: str, validate: Callable[[str], str | None]) -> str:
    return questionary.text(
        message,
        style=_PROMPT_STYLE,
        validate=_TextValidator(validate),
    ).unsafe_ask().strip()


def _ask_select(message: str, choices: list[Choice]) -> str:
    return questionary.select(
        message,
        choices=choices,
        style=_PROMPT_STYLE,
        use_arrow_keys=True,
        use_shortcuts=False,
    ).unsafe_ask()


def _ask_confirm(message: str) -> bool:
    return questionary.confirm(message, default=True, style=_PROMPT_STYLE).unsafe_ask()


def _show_step(console: Console, number: int, guidance: str) -> None:
    completed = "●" * number
    remaining = "○" * (_TOTAL_STEPS - number)
    console.print(f"\n[cyan]{completed}{remaining}[/cyan] [dim]Step {number}/{_TOTAL_STEPS}[/dim]")
    console.print(f"[dim]{guidance}[/dim]")


def _collect_job_draft(console: Console) -> JobDraft:
    _show_step(console, 1, "Use lowercase letters, numbers, and underscores.")
    name = _ask_text("Job name", validate_job_name)

    _show_step(console, 2, "Use ↑/↓ to choose, then press Enter.")
    job_type = _ask_select(
        "Job type",
        [
            Choice("Daily — scheduled every day at 09:00 UTC", value="daily"),
            Choice("One-time — runs once after merge to main", value="one_time"),
        ],
    )

    _show_step(console, 3, "Team or person responsible for this job.")
    owner = _ask_text(
        "Author / owner",
        lambda value: None if value.strip() else "Author / owner is required.",
    )

    _show_step(console, 4, "Separate multiple table names with commas.")
    tables_raw = _ask_text("Output tables", lambda value: validate_table_names(value)[1])
    output_tables, _ = validate_table_names(tables_raw)
    assert output_tables is not None

    _show_step(console, 5, "A short, one-line description of what the job does.")
    description = _ask_text(
        "Description",
        lambda value: None if value.strip() else "Description is required.",
    )

    return JobDraft(name, job_type, owner, output_tables, description)


def _show_header(console: Console) -> None:
    console.print(
        Panel.fit(
            Text("Create a transformation job", style="bold"),
            title="[bold cyan]SANDBOX[/bold cyan]",
            subtitle="[dim]Job wizard[/dim]",
            border_style="cyan",
            padding=(0, 2),
        )
    )


def _show_review(console: Console, draft: JobDraft, destination: Path) -> None:
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="dim", no_wrap=True)
    summary.add_column()
    summary.add_row("Job", draft.name)
    summary.add_row("Type", draft.job_type)
    summary.add_row("Author", draft.owner)
    summary.add_row("Tables", ", ".join(draft.output_tables))
    summary.add_row("Description", draft.description)
    summary.add_row("Location", str(destination))
    console.print()
    console.print(
        Panel.fit(
            summary,
            title="[bold cyan]Review[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        )
    )


def _show_status(console: Console, message: str, *, title: str, color: str) -> None:
    console.print()
    console.print(
        Panel.fit(
            message,
            title=f"[{color}]{title}[/{color}]",
            border_style=color,
            padding=(0, 1),
        )
    )


def run_job_init(jobs_root: Path, *, console: Console | None = None) -> bool:
    """Run the job-creation wizard. Return True only when a file is created."""
    console = console or Console()
    _show_header(console)

    try:
        draft = _collect_job_draft(console)
        destination = jobs_root / draft.job_type / f"{draft.name}.py"
        _show_review(console, draft, destination)
        if not _ask_confirm("Create this job?"):
            _show_status(
                console,
                "No files were changed.",
                title="Cancelled",
                color="bright_black",
            )
            return False
    except (EOFError, KeyboardInterrupt):
        _show_status(
            console,
            "No files were changed.",
            title="Cancelled",
            color="bright_black",
        )
        return False

    try:
        created = create_job(
            jobs_root=jobs_root,
            job_name=draft.name,
            job_type=draft.job_type,
            owner=draft.owner,
            output_tables=draft.output_tables,
            description=draft.description,
        )
    except (OSError, ValueError) as exc:
        _show_status(console, str(exc), title="Error", color="red")
        return False

    _show_status(
        console,
        f"Created [bold]{created}[/bold]\nNext: open the file and fill in main().",
        title="Success",
        color="green",
    )
    return True
