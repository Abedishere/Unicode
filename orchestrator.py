"""AI Agent Orchestrator — coordinate Claude Code and Codex CLI."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

import click
import yaml
from rich.console import Console

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
from utils.runner import CancelledByUser


PACKAGE_DIR = Path(__file__).resolve().parent

console = Console()

BANNER = r"""
[bold magenta]
  _   _ _   _ ___ ____ ___  ____  _____
 | | | | \ | |_ _/ ___/ _ \|  _ \| ____|
 | | | |  \| || | |  | | | | | | |  _|
 | |_| | |\  || | |__| |_| | |_| | |___
  \___/|_| \_|___\____\___/|____/|_____|
[/]
[dim]  Claude + Codex + Qwen — AI Agent Orchestrator[/]
[dim]  Type your task or pass it as an argument.[/]
[dim]  Press Ctrl+C twice to exit.[/]
"""

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


def _prompt_task() -> str:
    """Keep asking until the user gives a non-empty task."""
    while True:
        try:
            task = click.prompt(
                click.style("What do you want to build?", fg="cyan", bold=True),
                default="",
                show_default=False,
            )
        except (EOFError, click.Abort):
            continue
        if task.strip():
            return task.strip()
        console.print("[dim]Please enter a task.[/]")


def _run_task(
    task: str,
    cfg: dict,
    work_dir: str,
    claude: ClaudeAgent,
    codex: CodexAgent,
    qwen: QwenAgent,
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

    # ── Phase 1: Plan (Codex drafts, Claude reviews) ──
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

    # ── Phase 2: Discussion (only if admins disagree on the plan) ──
    if not agreed:
        log_info("Admins disagree — starting discussion to resolve.")
        result, extra = request_approval("discussion",
            "Claude and Codex disagree on the plan. They'll discuss for "
            "2 rounds to resolve.")
        if result == "proceed":
            if extra:
                task = f"{task}\n\nADDITIONAL USER INSTRUCTIONS:\n{extra}"
                log_info("Updated task with your instructions.")
            disc = _run_phase("Discussion",
                run_discussion, task, plan, claude, codex, 2)
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
    else:
        log_info("Admins agree — skipping discussion.")

    # ── Phase 3: Implementation (Claude as developer, with Qwen available) ──
    result, extra = request_approval("implement",
        "Claude (developer) will now implement the plan with full file access.\n"
        "Qwen is available for delegation on simple tasks.")
    if result == "proceed":
        if extra:
            plan = f"{plan}\n\nADDITIONAL USER INSTRUCTIONS:\n{extra}"
            log_info("Updated plan with your instructions.")
        impl = _run_phase("Implementation",
            run_implementation, task, plan, claude)
        if impl is not None:
            # Qwen writes orchestrator.md (project summary)
            _run_phase("Writing orchestrator.md",
                write_orchestrator_md, work_dir, task, plan, discussion, qwen)
    else:
        log_info("Skipping implementation phase.")

    # ── Phase 4: Code Review (Codex reviews, first is mandatory) ──
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
@click.option("--working-dir", default=None, help="Override working directory.")
def main(
    task: str | None,
    config_path: str,
    rounds: int | None,
    auto_commit: bool | None,
    working_dir: str | None,
):
    """Orchestrate Claude Code and Codex to collaboratively complete TASK."""
    # Install double Ctrl+C handler
    signal.signal(signal.SIGINT, _sigint_handler)

    console.print(BANNER)

    cfg = load_config(config_path)

    # CLI overrides
    if rounds is not None:
        cfg["discussion_rounds"] = rounds
    if auto_commit is not None:
        cfg["auto_commit"] = auto_commit
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

    log_info(f"Working directory: {work_dir}")

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

            _run_task(current_task, cfg, work_dir, claude, codex, qwen)

        except Exception as exc:
            log_error(f"Orchestrator failed: {exc}")
            console.print("[dim]Enter a new task or Ctrl+C twice to exit.[/]")
            continue


if __name__ == "__main__":
    main()
