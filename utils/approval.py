"""User approval gates for orchestrator actions."""

from __future__ import annotations

import click
from rich.console import Console
from rich.panel import Panel

console = Console()

# Actions that have been approved for the entire session
_session_approved: set[str] = set()


def reset_session_approvals() -> None:
    """Clear all session-wide auto-approvals (called between tasks)."""
    _session_approved.clear()


def request_approval(action: str, description: str) -> tuple[str, str | None]:
    """Ask the user for permission before proceeding with an action.

    Keeps asking until the user gives a definitive y/a/n answer.
    If they pick 'e' (edit), collects their instructions and loops back.

    Args:
        action: Short action key (e.g. "discussion", "implement", "commit").
        description: Human-readable explanation of what's about to happen.

    Returns:
        ("proceed", extra)  — user approved; extra is their edit text or None
        ("deny", None)      — user denied
    """
    # Already approved for this session
    if action in _session_approved:
        return "proceed", None

    extra_instructions: str | None = None

    while True:
        console.print()
        console.print(Panel(
            f"[bold]{description}[/]",
            title=f"[bold yellow]Approval needed: {action}[/]",
            border_style="yellow",
            expand=True,
        ))

        if extra_instructions:
            console.print(f"[dim]Your instructions so far:[/] {extra_instructions[:120]}...")

        choices = {
            "y": "Yes, proceed (one-time)",
            "a": "Auto-approve this action for the session",
            "e": "Pause — let me give instructions first",
            "n": "No, skip this step",
        }
        for key, label in choices.items():
            console.print(f"  [bold cyan]{key}[/] — {label}")

        console.print()
        choice = click.prompt(
            click.style("Your choice", fg="yellow", bold=True),
            type=click.Choice(list(choices.keys()), case_sensitive=False),
            default="y",
            show_choices=False,
        )

        if choice == "a":
            _session_approved.add(action)
            return "proceed", extra_instructions
        elif choice == "y":
            return "proceed", extra_instructions
        elif choice == "n":
            return "deny", None
        elif choice == "e":
            # Pause — collect user instructions, then loop back to ask again
            console.print()
            console.print("[bold yellow]Workflow paused.[/]")
            console.print("[dim]Type your instructions / changes. Press Enter twice to finish.[/]")
            console.print()

            lines: list[str] = []
            try:
                while True:
                    line = click.prompt(
                        click.style(">", fg="yellow", bold=True),
                        default="",
                        show_default=False,
                    )
                    if line == "" and (not lines or lines[-1] == ""):
                        break
                    lines.append(line)
            except (EOFError, click.Abort):
                pass

            text = "\n".join(lines).strip()
            if text:
                extra_instructions = text
                console.print(f"\n[bold green]Got it.[/] Now choose how to proceed:")
            else:
                console.print("\n[dim]No instructions entered.[/] Asking again:")
            # Loop back to the approval prompt


def require_approval(action: str, description: str) -> bool:
    """Simplified gate — returns True to proceed, False to skip."""
    result, _ = request_approval(action, description)
    return result == "proceed"
