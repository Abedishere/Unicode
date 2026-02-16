"""AI Agent Orchestrator — coordinate Claude Code and Codex CLI."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

import click
import yaml
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agents.claude_agent import ClaudeAgent
from agents.codex_agent import CodexAgent
from agents.qwen_agent import QwenAgent
from phases.discuss import run_discussion
from phases.implement import run_implementation
from phases.plan import consolidate_plan
from phases.review import run_review
from utils.approval import request_approval, reset_session_approvals
from utils.git_utils import commit, push, init_repo, is_git_repo
from utils.history import append_history, agent_update_md, init_agent_md, write_orchestrator_md
from utils.logger import init_transcript, log_error, log_info, log_phase, log_success
from utils.runner import CancelledByUser, TimeoutSkipToReview


PACKAGE_DIR = Path(__file__).resolve().parent

console = Console()

VERSION = "0.1.0"

# 3-color gradient stops: orange (Claude) → teal (Codex) → purple (Qwen)
_C = "#E8915C"  # Claude orange/amber
_X = "#50C8B4"  # Codex teal/blue-green
_Q = "#7B68EE"  # Qwen purple/blue

# Box-drawing block letters (Qwen Code style)
_art_lines = [
    "▄▄    ▄▄ ▄▄▄    ▄▄ ▄▄  ▄▄▄▄▄▄   ▄▄▄▄▄▄  ▄▄▄▄▄▄  ▄▄▄▄▄▄▄",
    "██║   ██║████╗  ██║██║██╔════╝ ██╔═══██╗██╔══██╗██╔════╝",
    "██║   ██║██╔██╗ ██║██║██║      ██║   ██║██║  ██║█████╗  ",
    "██║   ██║██║╚██╗██║██║██║      ██║   ██║██║  ██║██╔══╝  ",
    "╚██████╔╝██║ ╚████║██║╚██████╗ ╚██████╔╝██████╔╝███████╗",
    " ╚═════╝ ╚═╝  ╚═══╝╚═╝ ╚═════╝  ╚═════╝ ╚═════╝ ╚══════╝",
]


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> str:
    r = int(c1[0] + (c2[0] - c1[0]) * t)
    g = int(c1[1] + (c2[1] - c1[1]) * t)
    b = int(c1[2] + (c2[2] - c1[2]) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _gradient_char(col: int, width: int) -> str:
    """Return a hex color for column position using a 3-stop gradient."""
    stops = [_hex_to_rgb(_C), _hex_to_rgb(_X), _hex_to_rgb(_Q)]
    t = col / max(width - 1, 1)  # 0.0 → 1.0
    if t <= 0.5:
        return _lerp_color(stops[0], stops[1], t / 0.5)
    else:
        return _lerp_color(stops[1], stops[2], (t - 0.5) / 0.5)


def _gradient_line(line: str, width: int) -> str:
    """Apply per-character gradient to a line of art."""
    rich_line = ""
    for i, ch in enumerate(line):
        if ch == " ":
            rich_line += " "
        else:
            color = _gradient_char(i, width)
            rich_line += f"[bold {color}]{ch}[/]"
    return rich_line


def _build_gradient_art() -> str:
    """Build ASCII art with gradient coloring."""
    max_width = max(len(l) for l in _art_lines)
    return "\n".join(_gradient_line(line, max_width) for line in _art_lines)


ASCII_ART = _build_gradient_art()


def _print_banner(cfg: dict, work_dir: str) -> None:
    """Print the startup banner with ASCII art left and info box right."""
    console.print()

    # Info panel (right side)
    info_lines = Text()
    info_lines.append(">_ ", style="bold magenta")
    info_lines.append(f"Unicode Orchestrator", style="bold white")
    info_lines.append(f" (v{VERSION})\n\n", style="dim")
    info_lines.append(f"  Claude ", style=f"bold {_C}")
    info_lines.append(f"{cfg['claude_model']}", style="dim")
    info_lines.append(f"  |  ", style="dim")
    info_lines.append(f"Codex ", style=f"bold {_X}")
    info_lines.append(f"{cfg['codex_model']}", style="dim")
    info_lines.append(f"\n  Qwen ", style=f"bold {_Q}")
    info_lines.append(f"{cfg['qwen_model']}", style="dim")
    info_lines.append(f"\n  {work_dir}", style="dim")

    info_panel = Panel(
        info_lines,
        border_style="bright_black",
        padding=(0, 1),
    )

    console.print(
        Columns([ASCII_ART, info_panel], padding=(0, 2), expand=False),
    )

    console.print()
    console.print(
        "[dim]Tips: Type your task or pass it as an argument. "
        "Press Ctrl+C twice to exit.[/]"
    )
    console.print()

# Tracks when the last Ctrl+C was pressed for double-press detection
_last_ctrl_c: float = 0.0
_DOUBLE_PRESS_WINDOW = 2.0  # seconds


def _sigint_handler(signum, frame):
    """Handle Ctrl+C: first press does nothing, second press within 2s exits."""
    global _last_ctrl_c
    now = time.time()
    if now - _last_ctrl_c <= _DOUBLE_PRESS_WINDOW:
        console.print("\n[bold red]Ctrl+C ×2 — exiting unicode.[/]")
        os._exit(0)
    _last_ctrl_c = now
    console.print("\n[bold yellow]Press Ctrl+C again within 2s to exit.[/]")


def load_config(config_path: str | None) -> dict:
    defaults = {
        "discussion_rounds": 4,
        "max_review_iterations": 3,
        "claude_model": "opus",
        "codex_model": "gpt-5.3-codex",
        "qwen_model": "qwen3-coder",
        "timeout_seconds": 600,
        "codex_timeout_seconds": 300,
        "auto_commit": False,
        "allow_user_questions": True,
        "working_directory": ".",
    }
    # Resolve config: explicit path → CWD → package dir
    resolved = None
    if config_path:
        candidate = Path(config_path)
        if candidate.exists():
            resolved = candidate
        elif (PACKAGE_DIR / candidate.name).exists():
            resolved = PACKAGE_DIR / candidate.name
    if resolved:
        with open(resolved, "r", encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        defaults.update(overrides)
    return defaults


def _print_phase_banner(label: str, role: str, desc: str, color: str = "cyan") -> None:
    """Print a colored phase banner panel (like Qwen Code's notice boxes)."""
    content = Text()
    content.append(f"Talking to ", style="default")
    content.append(role, style=f"bold {color}")
    content.append(f" — {desc}", style="dim")
    console.print()
    console.print(Panel(
        content,
        title=f"[bold {color}]{label}[/]",
        border_style=color,
        padding=(0, 1),
    ))
    console.print()


def _run_phase(label: str, fn, *args, **kwargs):
    """Run a phase function, catching ESC cancellation gracefully.

    On ESC: pauses and asks retry / skip / clarify.
    Returns the function result, or None if skipped.
    """
    while True:
        try:
            return fn(*args, **kwargs)
        except CancelledByUser:
            console.print()
            console.print(f"[bold yellow]Paused:[/] {label}")
            console.print()
            choice = click.prompt(
                click.style("What now?", fg="yellow", bold=True),
                type=click.Choice(["retry", "clarify", "skip"], case_sensitive=False),
                default="retry",
            )
            if choice == "retry":
                console.print(f"[dim]Retrying {label} ...[/]")
                continue
            elif choice == "clarify":
                clarification = click.prompt(
                    click.style("Your instructions", fg="cyan", bold=True),
                    default="",
                    show_default=False,
                )
                if clarification.strip():
                    # Prepend clarification to the first string argument
                    new_args = list(args)
                    for i, a in enumerate(new_args):
                        if isinstance(a, str):
                            new_args[i] = f"{a}\n\nUSER CLARIFICATION:\n{clarification.strip()}"
                            break
                    args = tuple(new_args)
                console.print(f"[dim]Retrying {label} with your instructions ...[/]")
                continue
            else:
                log_info(f"Skipping {label}.")
                return None


def _prompt_gradient_line() -> str:
    """Build a gradient underline: orange → teal → purple."""
    w = console.width
    third = w // 3
    return (
        f"[{_C}]{'━' * third}[/]"
        f"[{_X}]{'━' * third}[/]"
        f"[{_Q}]{'━' * (w - 2 * third)}[/]"
    )


def _prompt_task() -> str:
    """Multiline task prompt with Qwen Code-style visuals.

    Shows a dim rule above, `> ` prompt, colored underline below.
    An empty line (double-Enter) or EOF submits the input.
    Prints `[N lines]` indicator for long inputs.
    """
    while True:
        lines: list[str] = []
        # Dim horizontal rule above prompt
        console.rule(style="bright_black")
        try:
            # First line with bold magenta `> ` prompt
            first = console.input("[bold magenta]> [/]")
            if first.strip():
                lines.append(first)
            else:
                console.print("[dim]Please enter a task.[/]")
                continue

            # Continuation lines with dim `... ` prefix
            while True:
                cont = console.input("[dim]... [/]")
                if not cont.strip():
                    break
                lines.append(cont)

        except EOFError:
            if not lines:
                continue

        task = "\n".join(lines).strip()
        if not task:
            console.print("[dim]Please enter a task.[/]")
            continue

        # Colored gradient underline after submission
        console.print(_prompt_gradient_line())

        if len(lines) > 3:
            console.print(f"[dim]  [{len(lines)} lines][/]")

        return task


def _load_saved_plan(work_dir: str) -> str:
    """Try to load a previously saved plan from .orchestrator/plan.md."""
    plan_path = Path(work_dir) / ".orchestrator" / "plan.md"
    if plan_path.exists():
        plan = plan_path.read_text(encoding="utf-8").strip()
        if plan:
            log_info(f"Loaded saved plan from {plan_path}")
            return plan
    log_info("No saved plan found — proceeding without one.")
    return ""


def _run_task(
    task: str,
    cfg: dict,
    work_dir: str,
    claude: ClaudeAgent,
    codex: CodexAgent,
    qwen: QwenAgent,
    phase: str = "all",
) -> None:
    """Execute one full orchestration run for the given task."""
    # Reset per-session approvals for each new task
    reset_session_approvals()

    transcript_path = init_transcript(work_dir)
    log_info(f"Transcript: {transcript_path}")
    log_info(f"Task: {task}")
    start_time = time.time()

    # Track state across phases
    discussion: list[dict[str, str]] = []
    plan = ""
    approved = False
    skip_to_review = False

    # For standalone implement/review, load saved plan
    if phase in ("implement", "review"):
        plan = _load_saved_plan(work_dir)

    # ── Phase 1: Plan (Codex drafts, Claude reviews) ──
    run_plan = phase in ("all", "plan", "discuss")
    if run_plan and phase == "all":
        _print_phase_banner("Planning", "admins", "Claude & Codex will draft the plan", "cyan")
    if run_plan:
        result, extra = request_approval("plan",
            "Codex (GPT) will draft a plan, then Claude will review it.")
        if result == "proceed":
            if extra:
                task = f"{task}\n\nADDITIONAL USER INSTRUCTIONS:\n{extra}"
                log_info("Updated task with your instructions.")
            plan_result = _run_phase("Plan",
                consolidate_plan, task, claude, codex, work_dir)
            if plan_result is not None:
                plan, agreed = plan_result
            else:
                plan, agreed = "", True
        else:
            log_info("Skipping plan phase.")
            agreed = True
    else:
        agreed = True

    # ── Phase 2: Discussion (only if admins disagree on the plan) ──
    run_discuss = phase in ("all", "discuss")
    if run_discuss and not agreed and phase == "all":
        _print_phase_banner("Discussion", "admins", "Claude & Codex will discuss the plan", "cyan")
    if run_discuss and not agreed:
        log_info("Admins disagree — starting discussion to resolve.")
        result, extra = request_approval("discussion",
            "Claude and Codex disagree on the plan. They'll discuss for "
            "2 rounds to resolve.")
        if result == "proceed":
            if extra:
                task = f"{task}\n\nADDITIONAL USER INSTRUCTIONS:\n{extra}"
                log_info("Updated task with your instructions.")
            disc = _run_phase("Discussion",
                run_discussion, task, plan, claude, codex, 2,
                allow_user_questions=cfg.get("allow_user_questions", True))
            if disc is not None:
                discussion = disc

            # Re-plan after discussion
            log_info("Re-planning after discussion ...")
            plan_result = _run_phase("Re-Plan",
                consolidate_plan, task, claude, codex, work_dir, discussion)
            if plan_result is not None:
                plan, _ = plan_result
        else:
            log_info("Skipping discussion — proceeding with current plan.")
    elif not agreed:
        log_info("Admins agree — skipping discussion.")
    else:
        log_info("Admins agree — skipping discussion.")

    # Stop here if the user only wanted plan or discuss
    if phase in ("plan", "discuss"):
        log_phase("Phase complete.")
        duration = time.time() - start_time
        mins, secs = divmod(int(duration), 60)
        log_info(f"Finished in {mins}m {secs:02d}s.")
        return

    # ── Phase 3: Implementation (Claude as developer, with Qwen available) ──
    run_impl = phase in ("all", "implement")
    if run_impl and phase == "all":
        _print_phase_banner("Implementation", "developer", "Claude Code will implement the plan", "magenta")
    if run_impl:
        result, extra = request_approval("implement",
            "Claude (developer) will now implement the plan with full file access.\n"
            "Qwen is available for delegation on simple tasks.")
        if result == "proceed":
            if extra:
                plan = f"{plan}\n\nADDITIONAL USER INSTRUCTIONS:\n{extra}"
                log_info("Updated plan with your instructions.")
            try:
                impl = _run_phase("Implementation",
                    run_implementation, task, plan, claude)
                if impl is not None:
                    # Qwen writes orchestrator.md (project summary)
                    _run_phase("Writing orchestrator.md",
                        write_orchestrator_md, work_dir, task, plan, discussion, qwen)
            except TimeoutSkipToReview:
                log_info("Skipping to review phase (user request after timeout).")
                skip_to_review = True
        else:
            log_info("Skipping implementation phase.")

    # Stop here if the user only wanted implement (and didn't skip to review)
    if phase == "implement" and not skip_to_review:
        log_phase("Implementation phase complete.")
        duration = time.time() - start_time
        mins, secs = divmod(int(duration), 60)
        log_info(f"Finished in {mins}m {secs:02d}s.")
        return

    # ── Phase 4: Code Review (Codex reviews, first is mandatory) ──
    if phase == "all":
        _print_phase_banner("Code Review", "reviewer", "Codex will review the implementation", "green")
    log_info("First code review is mandatory.")
    rev = _run_phase("Code Review",
        run_review, task, plan, claude, codex, work_dir,
        cfg["max_review_iterations"])
    approved = rev if rev is not None else True

    # ── Phase 5: Finalization ──
    log_phase("Phase 5: Finalization")
    outcome = "APPROVED" if approved else "NOT APPROVED"

    if approved:
        log_success("Implementation approved!")

        # Each agent updates its own MD file
        _run_phase("Claude updating CLAUDE.md",
            agent_update_md, work_dir, task, plan, discussion, claude, "CLAUDE.md")
        _run_phase("Codex updating AGENTS.md",
            agent_update_md, work_dir, task, plan, discussion, codex, "AGENTS.md")

        # Codex writes the commit message
        commit_prompt = (
            "Write a short git commit message for these changes. "
            "One line, max 72 characters. No quotes. No prefix. "
            "Just describe what was done.\n\n"
            f"TASK: {task}\n\n"
            f"PLAN:\n{plan[:1000]}\n"
        )
        log_info("Codex is writing commit message ...")
        commit_msg = _run_phase("Commit message", codex.query, commit_prompt)
        if not commit_msg:
            commit_msg = f"orchestrator: {task[:72]}"
        # Clean up — take first line only, strip quotes
        commit_msg = commit_msg.strip().split("\n")[0].strip('"').strip("'").strip()

        # User approves the commit message
        result, extra = request_approval("git-commit",
            f"Commit & push with message:\n\"{commit_msg}\"")
        if result == "proceed":
            if extra:
                commit_msg = extra  # user rewrote the message
            commit(commit_msg, work_dir)
            log_success(f"Changes committed: {commit_msg}")
            push(work_dir)
            log_success("Pushed to remote.")
        else:
            log_info("Commit skipped — changes are in your working directory.")
    else:
        log_error("Implementation was NOT approved after all review iterations.")

    # Record history (Qwen summarizes, not Claude)
    duration = time.time() - start_time
    summary_prompt = (
        f"TASK: {task}\n\nPLAN:\n{plan}\n\n"
        "List files created/modified as a bullet list. One line each. No commentary."
    )
    log_info("Qwen is summarizing actions ...")
    actions_summary = _run_phase("Summary", qwen.query, summary_prompt)
    if not actions_summary:
        actions_summary = "- Summary skipped"

    transcript_name = transcript_path.name
    append_history(work_dir, task, outcome, duration, actions_summary, transcript_name)
    log_info("Appended run to .orchestrator/history.md")

    if approved:
        log_success("Task complete!")
    else:
        log_error("Task finished — implementation was not approved.")


@click.command()
@click.argument("task", required=False, default=None)
@click.option("--config", "config_path", default="config.yaml", help="Config file path.")
@click.option("--rounds", type=int, default=None, help="Override discussion rounds.")
@click.option("--auto-commit", is_flag=True, default=None, help="Auto-commit on approval.")
@click.option("--no-questions", is_flag=True, default=False, help="Disable admin questions to user during discussion.")
@click.option("--working-dir", default=None, help="Override working directory.")
@click.option(
    "--phase", default="all",
    type=click.Choice(["all", "plan", "discuss", "implement", "review"], case_sensitive=False),
    help="Run only a specific phase.",
)
def main(
    task: str | None,
    config_path: str,
    rounds: int | None,
    auto_commit: bool | None,
    no_questions: bool,
    working_dir: str | None,
    phase: str,
):
    """Orchestrate Claude Code and Codex to collaboratively complete TASK."""
    # Install double Ctrl+C handler
    signal.signal(signal.SIGINT, _sigint_handler)

    cfg = load_config(config_path)

    # CLI overrides
    if rounds is not None:
        cfg["discussion_rounds"] = rounds
    if auto_commit is not None:
        cfg["auto_commit"] = auto_commit
    if no_questions:
        cfg["allow_user_questions"] = False
    if working_dir is not None:
        cfg["working_directory"] = working_dir

    work_dir = os.path.abspath(cfg["working_directory"])
    os.makedirs(work_dir, exist_ok=True)

    # Ensure git repo exists
    if not is_git_repo(work_dir):
        init_repo(work_dir)
        log_info(f"Initialized git repo in {work_dir}")

    # Initialize agent MD files (header + orchestrator.md reference)
    init_agent_md(work_dir)

    # Print the banner with info box
    _print_banner(cfg, work_dir)

    # Create agents
    claude = ClaudeAgent(
        model=cfg["claude_model"],
        timeout=cfg["timeout_seconds"],
        working_dir=work_dir,
    )
    codex = CodexAgent(
        model=cfg["codex_model"],
        timeout=cfg["codex_timeout_seconds"],
        working_dir=work_dir,
    )
    qwen = QwenAgent(
        model=cfg["qwen_model"],
        timeout=cfg["timeout_seconds"],
        working_dir=work_dir,
    )

    # Show phase banner immediately on startup when a specific phase is selected
    if phase != "all":
        _phase_banners = {
            "plan":      ("Planning",       "admins",   "Claude & Codex will draft the plan",      "cyan"),
            "discuss":   ("Discussion",     "admins",   "Claude & Codex will discuss the plan",    "cyan"),
            "implement": ("Implementation", "developer","Claude Code (developer) will implement",  "magenta"),
            "review":    ("Code Review",    "reviewer", "Codex will review the implementation",    "green"),
        }
        label, role, desc, color = _phase_banners.get(phase, (phase, "agents", "", "cyan"))
        _print_phase_banner(label, role, desc, color)

    # ── Main loop: keep accepting tasks until double Ctrl+C ──
    first_task = task  # from CLI argument, if any

    while True:
        try:
            if first_task:
                current_task = first_task
                first_task = None  # only use CLI arg for the first run
            else:
                console.print()
                current_task = _prompt_task()

            _run_task(current_task, cfg, work_dir, claude, codex, qwen, phase)

        except Exception as exc:
            log_error(f"Orchestrator failed: {exc}")
            console.print("[dim]Enter a new task or Ctrl+C twice to exit.[/]")
            continue


if __name__ == "__main__":
    main()
