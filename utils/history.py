"""Utilities for persistent project history and per-agent MD files."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agents.base import BaseAgent
from utils.logger import log_info


def append_history(
    working_dir: str,
    task: str,
    outcome: str,
    duration_secs: float,
    actions_summary: str,
    transcript_name: str,
) -> Path:
    """Append a run entry to .orchestrator/history.md."""
    history_dir = Path(working_dir) / ".orchestrator"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / "history.md"

    mins, secs = divmod(int(duration_secs), 60)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entry = (
        "\n---\n"
        f"## Run: {timestamp}\n"
        f"**Task:** {task}\n"
        f"**Outcome:** {outcome}\n"
        f"**Duration:** {mins}m {secs:02d}s\n"
        f"**Actions taken:**\n{actions_summary}\n"
        f"**Transcript:** {transcript_name}\n"
        "---\n"
    )

    if not history_path.exists():
        history_path.write_text(
            "# Orchestrator Run History\n\n"
            "Automatically maintained by AI Orchestrator.\n",
            encoding="utf-8",
        )

    with open(history_path, "a", encoding="utf-8") as f:
        f.write(entry)

    return history_path


_CLAUDE_MD_HEADER = (
    "# Project Context (Claude Code)\n\n"
    "Managed by the AI Orchestrator. Claude Code reads this file on startup.\n\n"
    "## Project Architecture\n"
    "See `orchestrator.md` in this directory for a full project summary, "
    "folder structure, architecture overview, and notes on what each "
    "component does.\n\n"
)

_AGENTS_MD_HEADER = (
    "# Project Context (Codex)\n\n"
    "Managed by the AI Orchestrator. Codex CLI reads this file on startup.\n\n"
    "## Project Architecture\n"
    "See `orchestrator.md` in this directory for a full project summary, "
    "folder structure, architecture overview, and notes on what each "
    "component does.\n\n"
)


def init_agent_md(working_dir: str) -> None:
    """Create CLAUDE.md and AGENTS.md with persistent headers if they don't exist."""
    work = Path(working_dir)

    claude_md = work / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(_CLAUDE_MD_HEADER, encoding="utf-8")

    agents_md = work / "AGENTS.md"
    if not agents_md.exists():
        agents_md.write_text(_AGENTS_MD_HEADER, encoding="utf-8")


def agent_update_md(
    working_dir: str,
    task: str,
    plan: str,
    discussion: list[dict[str, str]],
    agent: BaseAgent,
    target: str,
) -> None:
    """Have an agent write its own MD file.

    *target* is either ``"CLAUDE.md"`` or ``"AGENTS.md"``.
    The agent receives the task/plan/discussion context and rewrites the
    file while preserving the persistent header that references
    ``orchestrator.md``.
    """
    work = Path(working_dir)
    md_path = work / target

    header = _CLAUDE_MD_HEADER if target == "CLAUDE.md" else _AGENTS_MD_HEADER

    transcript = "\n".join(
        f"[{e['agent']}]: {e['message']}" for e in discussion
    )

    prompt = (
        f"TASK: {task}\n\n"
        f"IMPLEMENTATION PLAN:\n{plan[:3000]}\n\n"
        f"DISCUSSION:\n{transcript[:3000]}\n\n"
        f"You are updating {target} — a project context file that your CLI "
        "reads on startup for future sessions.\n"
        "Write the BODY of this file (the header is added automatically). Include:\n"
        "- Latest task and outcome\n"
        "- Key decisions and conventions from the discussion\n"
        "- Important implementation details and patterns\n"
        "- Anything useful for picking up this project next time\n\n"
        "Be concise. Bullet points. No preamble."
    )

    log_info(f"{agent.name} is updating {target} ...")
    body = agent.query(prompt)

    md_path.write_text(header + body + "\n", encoding="utf-8")
    log_info(f"{target} updated by {agent.name}")


def write_orchestrator_md(
    working_dir: str,
    task: str,
    plan: str,
    discussion: list[dict[str, str]],
    qwen: BaseAgent,
) -> None:
    """Have Qwen write orchestrator.md — a project summary with architecture,
    folder structure, and key information for anyone (human or AI) picking
    up the project.
    """
    work = Path(working_dir)

    transcript = "\n".join(
        f"[{e['agent']}]: {e['message']}" for e in discussion
    )

    prompt = (
        f"TASK: {task}\n\n"
        f"IMPLEMENTATION PLAN:\n{plan}\n\n"
        f"DISCUSSION:\n{transcript[:3000]}\n\n"
        "Write a project summary file called orchestrator.md. Include:\n"
        "1. **Project Overview** — what this project is, one paragraph\n"
        "2. **Architecture** — how the code is organized, key patterns\n"
        "3. **Folder Structure** — what each folder/key file does\n"
        "4. **Tech Stack** — languages, frameworks, tools used\n"
        "5. **Good to Know** — quirks, conventions, or gotchas\n\n"
        "Write it as clean Markdown. Be concise but thorough. "
        "This file will be read by both humans and AI tools to understand the project."
    )

    log_info("Qwen is writing orchestrator.md ...")
    content = qwen.query(prompt)

    orch_md = work / "orchestrator.md"
    orch_md.write_text(content, encoding="utf-8")
    log_info(f"orchestrator.md written to {orch_md}")
