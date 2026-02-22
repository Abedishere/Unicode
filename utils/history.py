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


_MEMORY_PROTOCOL_SECTION = (
    "## Project Memory System\n\n"
    "This project maintains institutional knowledge in `docs/project_notes/` "
    "for consistency across sessions.\n\n"
    "### Memory Files\n\n"
    "- **bugs.md** — Bug log with dates, root causes, solutions, and prevention notes\n"
    "- **decisions.md** — Architectural Decision Records (ADRs) with context and trade-offs\n"
    "- **key_facts.md** — Project configuration, credentials, ports, important URLs\n"
    "- **issues.md** — Work log with task descriptions and outcomes\n\n"
    "### Memory-Aware Protocols\n\n"
    "**Before proposing architectural changes:**\n"
    "- Check `docs/project_notes/decisions.md` for existing decisions\n"
    "- Verify the proposed approach doesn't conflict with past choices\n"
    "- If it does conflict, acknowledge the existing decision and explain why a change is warranted\n\n"
    "**When encountering errors or bugs:**\n"
    "- Search `docs/project_notes/bugs.md` for similar issues\n"
    "- Apply known solutions if found\n"
    "- Document new bugs and their solutions when resolved\n\n"
    "**When looking up project configuration:**\n"
    "- Check `docs/project_notes/key_facts.md` for credentials, ports, URLs, service accounts\n"
    "- Prefer documented facts over assumptions\n\n"
    "**When completing work:**\n"
    "- Outcomes are logged automatically by the orchestrator in `docs/project_notes/issues.md`\n\n"
)

_CLAUDE_MD_HEADER = (
    "# Project Context (Claude Code)\n\n"
    "Managed by the AI Orchestrator. Claude Code reads this file on startup.\n\n"
    "## Project Architecture\n"
    "See `orchestrator.md` in this directory for a full project summary, "
    "folder structure, architecture overview, and notes on what each "
    "component does.\n\n"
    + _MEMORY_PROTOCOL_SECTION
)

_AGENTS_MD_HEADER = (
    "# Project Context (Codex)\n\n"
    "Managed by the AI Orchestrator. Codex CLI reads this file on startup.\n\n"
    "## Project Architecture\n"
    "See `orchestrator.md` in this directory for a full project summary, "
    "folder structure, architecture overview, and notes on what each "
    "component does.\n\n"
    + _MEMORY_PROTOCOL_SECTION
)


_MAX_BODY_WORDS = 400  # hard cap enforced in code after the agent responds


def _enforce_word_limit(text: str, max_words: int = _MAX_BODY_WORDS) -> str:
    """Truncate *text* to *max_words* words, cutting at the last newline before
    the limit so we never break mid-sentence or mid-bullet."""
    words = text.split()
    if len(words) <= max_words:
        return text
    # Find the character position of word max_words
    pos = 0
    for word in words[:max_words]:
        pos = text.index(word, pos) + len(word)
    # Cut back to the last newline so we don't leave a ragged line
    cut = text.rfind("\n", 0, pos)
    trimmed = text[:cut].rstrip() if cut != -1 else text[:pos].rstrip()
    return trimmed + "\n\n*(body trimmed to stay within the 400-word limit)*"


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
    """Have an agent synthesize and rewrite a project context MD file.

    *target* is either ``"CLAUDE.md"`` or ``"AGENTS.md"``.

    The existing file body is read and passed to the agent alongside the new
    task context.  The agent produces a single unified, condensed body that
    merges old knowledge with new — superseded info is dropped, still-relevant
    info is kept, and duplicates are removed.  The persistent header is always
    preserved and never overwritten.
    """
    work = Path(working_dir)
    md_path = work / target
    header = _CLAUDE_MD_HEADER if target == "CLAUDE.md" else _AGENTS_MD_HEADER

    # Read and strip the persistent header to get just the previous body.
    existing_body = ""
    if md_path.exists():
        try:
            raw = md_path.read_text(encoding="utf-8")
            existing_body = raw[len(header):].strip() if raw.startswith(header) else raw.strip()
        except OSError:
            pass

    transcript = "\n".join(
        f"[{e['agent']}]: {e['message']}" for e in discussion
    )

    prompt = (
        f"TASK JUST COMPLETED: {task}\n\n"
        f"IMPLEMENTATION PLAN:\n{plan[:2500]}\n\n"
        f"DISCUSSION TRANSCRIPT:\n{transcript[:1500]}\n\n"
        f"EXISTING {target} BODY (accumulated from previous tasks):\n"
        f"{existing_body if existing_body else '(empty — first run)'}\n\n"
        f"You are rewriting the body of {target}, a project context file read by "
        "AI agents on startup.\n\n"
        "Produce a SINGLE SYNTHESIZED body that combines existing knowledge with "
        "what was learned from this task. Rules:\n"
        "- Do NOT append — merge and rewrite as one unified document.\n"
        "- If new info supersedes old, keep the new version only.\n"
        "- If both old and new are still relevant, merge them into one point.\n"
        "- Remove all redundancy and repetition.\n"
        "- Capture: key architectural decisions, conventions, patterns, "
        "anti-patterns, current project state, and implementation details "
        "an agent needs to resume work.\n"
        "- Bullet points. No preamble. Max ~400 words."
    )

    log_info(f"{agent.name} is updating {target} ...")
    try:
        body = agent.query(prompt)
        if body.strip():
            word_count = len(body.split())
            if word_count > _MAX_BODY_WORDS:
                log_info(
                    f"{target}: response was {word_count} words — "
                    f"trimming to {_MAX_BODY_WORDS}."
                )
                body = _enforce_word_limit(body)
            else:
                log_info(f"{target}: {word_count} words — within limit.")
            md_path.write_text(header + body + "\n", encoding="utf-8")
            log_info(f"{target} updated by {agent.name}")
        else:
            log_info(f"Warning: {agent.name} returned empty body for {target} — skipping update")
    except Exception as exc:
        log_info(f"Warning: could not update {target}: {exc}")


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
