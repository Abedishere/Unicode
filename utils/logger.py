from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

AGENT_STYLES = {
    "Claude": "bold cyan",
    "Codex": "bold green",
    "Kiro": "bold magenta",
    "User": "bold white",
    "System": "bold yellow",
}


def format_transcript(discussion: list[dict[str, str]]) -> str:
    """Render a discussion history as a plain-text transcript."""
    return "\n".join(f"[{e['agent']}]: {e['message']}" for e in discussion)


def skills_block(ctx: str) -> str:
    """Wrap skills context in a <skills> XML block for prompt injection."""
    return f"<skills>\n{ctx}\n</skills>\n\n" if ctx else ""


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


def log_memory_context(context: str) -> None:
    """Print injected memory context in a yellow panel before agents receive it."""
    try:
        console.print(Panel(
            context,
            title="[bold yellow]Memory Context[/]",
            border_style="yellow",
        ))
        _write_log(f"[MEMORY_CONTEXT]: {context[:500]}")
    except Exception:
        pass


def log_phase_outcome(phase: str, data: dict) -> None:
    """Print a blue summary panel after each phase completes. Never raises."""
    try:
        if phase == "research":
            brief = (data.get("brief") or "")[:200]
            body = brief or "(no brief)"
        elif phase == "discussion":
            history = data.get("discussion") or []
            entries = [e for e in history if len(e.get("message", "")) > 20][-3:]
            bullets = [f"• [{e['agent']}]: {e['message'][:120]}" for e in entries]
            body = "\n".join(bullets) if bullets else "(no discussion)"
        elif phase == "plan":
            files = data.get("files") or []
            if files:
                t = Table(show_header=True, header_style="bold", box=None)
                t.add_column("Action")
                t.add_column("File")
                for f in files:
                    t.add_row(f.action, f.path)
                console.print(Panel(
                    t,
                    title="[bold blue]Phase Outcome: Plan[/]",
                    border_style="blue",
                ))
                _write_log(f"[OUTCOME:plan] {len(files)} file(s)")
                return
            else:
                body = "(monolithic — no file list)"
        elif phase == "review":
            verdict = data.get("verdict", "UNKNOWN")
            count = data.get("issue_count", 0)
            body = f"{verdict} | Issues: {count}"
        elif phase == "memory":
            counts = data.get("counts") or {}
            body = "\n".join(f"• {k}: {v}" for k, v in counts.items())
            body = body or "(no counts)"
        else:
            body = str(data)

        console.print(Panel(
            body,
            title=f"[bold blue]Phase Outcome: {phase.title()}[/]",
            border_style="blue",
        ))
        _write_log(f"[OUTCOME:{phase}] {body[:200]}")
    except Exception:
        pass


