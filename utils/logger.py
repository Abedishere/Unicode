from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

AGENT_STYLES = {
    "Claude": "bold cyan",
    "Codex": "bold green",
    "Qwen": "bold magenta",
    "User": "bold white",
    "System": "bold yellow",
}


def format_transcript(discussion: list[dict[str, str]]) -> str:
    """Render a discussion history as a plain-text transcript."""
    return "\n".join(f"[{e['agent']}]: {e['message']}" for e in discussion)


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as '2m 05s' or '45s'."""
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}m {secs:02d}s" if mins else f"{secs}s"


_transcript_path: Path | None = None


def init_transcript(working_dir: str) -> Path:
    """Create the transcript log file and return its path."""
    global _transcript_path
    orch_dir = Path(working_dir) / ".orchestrator"
    orch_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _transcript_path = orch_dir / f"transcript_{timestamp}.log"
    _transcript_path.touch()
    return _transcript_path


def _write_log(text: str) -> None:
    if _transcript_path is not None:
        with open(_transcript_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def log_phase(phase_name: str) -> None:
    """Print a prominent phase header."""
    console.rule(f"[bold magenta]{phase_name}[/]")
    _write_log(f"\n{'='*60}\n{phase_name}\n{'='*60}")


def log_agent(agent_name: str, message: str) -> None:
    """Print an agent's message with a colored label."""
    style = AGENT_STYLES.get(agent_name, "bold white")
    panel = Panel(
        Text(message),
        title=f"[{style}]{agent_name}[/]",
        border_style=style.replace("bold ", ""),
        expand=True,
    )
    console.print(panel)
    _write_log(f"[{agent_name}]: {message}")


def log_info(message: str) -> None:
    console.print(f"[dim]{message}[/]")
    _write_log(f"[INFO]: {message}")


def log_success(message: str) -> None:
    console.print(f"[bold green]{message}[/]")
    _write_log(f"[SUCCESS]: {message}")


def log_error(message: str) -> None:
    console.print(f"[bold red]{message}[/]")
    _write_log(f"[ERROR]: {message}")
