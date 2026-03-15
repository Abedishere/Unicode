"""Phase 1: Discussion — Claude and Codex agree on an approach.

They discuss freely and vote by ending their message with AGREED once
both are satisfied. The loop exits early as soon as both have agreed.
"""

from __future__ import annotations

import re

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agents.base import BaseAgent
from utils.logger import format_transcript, log_agent, log_info, log_phase

console = Console()

# Patterns that indicate an agent is asking the USER a question
_USER_QUESTION = re.compile(
    r"(@user|user[,:]?\s|do you prefer|would you like|could you clarify"
    r"|do you want|what do you think|your preference|please confirm"
    r"|should we|can you tell|let us know|need your input)",
    re.IGNORECASE,
)

# Sentinel pattern: agents are instructed to end their message with "AGREED"
_AGREEMENT = re.compile(r"\bAGREED\b|\bI agree\b")


def _has_user_question(text: str) -> bool:
    """Check if an agent's reply contains a question directed at the user."""
    return bool(_USER_QUESTION.search(text))


def _has_agreement(text: str) -> bool:
    """Check if an agent's reply signals consensus."""
    return bool(_AGREEMENT.search(text))


def _ask_user(agent_name: str, message: str) -> str | None:
    """Pause and collect user input when agents have questions."""
    console.print()
    console.print(Panel(
        Text(f"{agent_name} is asking you a question.\nPress Enter to skip."),
        title="[bold yellow]Your input needed[/]",
        border_style="yellow",
    ))
    console.print()

    try:
        answer = click.prompt(
            click.style("You", fg="white", bold=True),
            default="",
            show_default=False,
        )
    except (EOFError, click.Abort):
        return None

    return answer.strip() if answer.strip() else None


def _summarize_old_history(
    history: list[dict[str, str]],
    keep_recent: int = 4,
) -> tuple[str, list[dict[str, str]]]:
    """Split history into a compact summary of old entries and recent verbatim entries.

    Returns (summary_text, recent_entries).  If the history is short enough,
    returns ("", history) unchanged.
    """
    if len(history) <= keep_recent:
        return "", history

    old = history[:-keep_recent]
    recent = history[-keep_recent:]

    lines = []
    for entry in old:
        agent = entry.get("agent", "")
        msg = entry.get("message", "")
        snippet = msg[:150].replace("\n", " ").strip()
        if len(msg) > 150:
            snippet += "..."
        lines.append(f"  [{agent}]: {snippet}")

    return "\n".join(lines), recent


def run_discussion(
    task: str,
    claude: BaseAgent,
    codex: BaseAgent,
    max_rounds: int = 2,
    user_context: str | None = None,
    allow_user_questions: bool = True,
    repo_map: str = "",
) -> tuple[list[dict[str, str]], bool]:
    """Run a multi-round discussion between Claude and Codex.

    Runs up to *max_rounds* rounds but exits early once both agents signal
    agreement (by including an agreement phrase in their message).

    Returns (history, agreed) where agreed=True means both agents
    converged on an approach.
    """
    log_phase(f"Phase 1: Discussion (up to {max_rounds} rounds)")
    history: list[dict[str, str]] = []

    if user_context:
        history.append({"agent": "User", "message": user_context})
        log_info("User context injected into discussion.")

    agreed = False
    for round_num in range(1, max_rounds + 1):
        log_info(f"Round {round_num}/{max_rounds}")

        # Only allow user questions after the first full round
        can_ask = allow_user_questions and round_num > 1

        # --- Codex's turn (goes first) ---
        codex_prompt = _build_prompt(task, history, codex.name, claude.name, max_rounds, repo_map)
        log_info(f"Waiting for {codex.name} ...")
        codex_reply = codex.query(codex_prompt)
        history.append({"agent": codex.name, "message": codex_reply})
        log_agent(codex.name, codex_reply)

        if can_ask and _has_user_question(codex_reply):
            answer = _ask_user(codex.name, codex_reply)
            if answer:
                history.append({"agent": "User", "message": answer})
                log_agent("User", answer)

        # --- Claude's turn (goes second) ---
        claude_prompt = _build_prompt(task, history, claude.name, codex.name, max_rounds, repo_map)
        log_info(f"Waiting for {claude.name} ...")
        claude_reply = claude.query(claude_prompt)
        history.append({"agent": claude.name, "message": claude_reply})
        log_agent(claude.name, claude_reply)

        if can_ask and _has_user_question(claude_reply):
            answer = _ask_user(claude.name, claude_reply)
            if answer:
                history.append({"agent": "User", "message": answer})
                log_agent("User", answer)

        # Check if both agents agree — exit early if so
        if _has_agreement(codex_reply) and _has_agreement(claude_reply):
            agreed = True
            log_info(f"Both agents agree after round {round_num} — stopping early.")
            break

    if not agreed:
        log_info("Discussion complete — proceeding with best available approach.")

    return history, agreed


def _build_prompt(
    task: str,
    history: list[dict[str, str]],
    current_agent: str,
    other_agent: str,
    max_rounds: int,
    repo_map: str = "",
) -> str:
    lines = [
        f"You are {current_agent}, a senior technical lead (admin).",
        f"You are collaborating with {other_agent} (another admin) on this task.",
        "A separate developer will implement whatever you two agree on.",
        "You do NOT write code, create files, or delegate tasks. You may read the repo. You only discuss.",
        f"\nTASK: {task}\n",
    ]
    if repo_map:
        lines.append("CODEBASE SKELETON:")
        lines.append(repo_map)
        lines.append("")

    lines.extend([
        "Your goal: reach agreement on the best implementation approach.",
        f"IMPORTANT: You have a MAXIMUM of {max_rounds} rounds. "
        "Once you are satisfied with the agreed approach, end your message with: AGREED\n",
    ])

    if history:
        summary, recent = _summarize_old_history(history)
        if summary:
            lines.append("EARLIER DISCUSSION (summary):")
            lines.append(summary)
            lines.append("")
            lines.append("RECENT DISCUSSION:")
            lines.append(format_transcript(recent))
        else:
            lines.append("CONVERSATION SO FAR:")
            lines.append(format_transcript(history))
        lines.append("")

    lines.append(
        "RULES:\n"
        "- You are an ADMIN. You do NOT write code, create files, or delegate to anyone.\n"
        "- Discuss the approach: files to touch, architecture, key decisions.\n"
        "- Be concise. Bullet points, not essays.\n"
        "- Make decisions, don't ramble about options.\n"
        "- If you need input from the user, prefix with @User and ask directly.\n"
        "- When you are happy with the agreed approach, end your message with: AGREED\n"
        "- No philosophizing. No restating the task. Just actionable output.\n"
    )
    return "\n".join(lines)
