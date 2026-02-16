"""Phase 1: Discussion — Claude and Codex discuss the task.

If either agent has questions for the user, the conversation pauses
and the user can answer before it continues.
"""

from __future__ import annotations

import re

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agents.base import BaseAgent
from utils.logger import log_agent, log_info, log_phase

console = Console()

# Patterns that indicate an agent is asking the USER a question
_USER_QUESTION = re.compile(
    r"(@user|user[,:]?\s|do you prefer|would you like|could you clarify"
    r"|do you want|what do you think|your preference|please confirm"
    r"|should we|can you tell|let us know|need your input)",
    re.IGNORECASE,
)


def _has_user_question(text: str) -> bool:
    """Check if an agent's reply contains a question directed at the user."""
    return bool(_USER_QUESTION.search(text))


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


def run_discussion(
    task: str,
    plan: str,
    claude: BaseAgent,
    codex: BaseAgent,
    rounds: int = 2,
    user_context: str | None = None,
    allow_user_questions: bool = True,
) -> list[dict[str, str]]:
    """Run a multi-round discussion between Claude and Codex.

    Max 2 rounds — they must reach a decision within that limit.
    Returns the conversation history as a list of
    {"agent": name, "message": text} dicts.

    If allow_user_questions is False, agents never pause to ask the user.
    Otherwise, user questions are only allowed after at least one full
    round of back-and-forth (both agents have spoken once).
    """
    log_phase("Phase 2: Discussion (max 2 rounds)")
    history: list[dict[str, str]] = []

    if user_context:
        history.append({"agent": "User", "message": user_context})
        log_info("User context injected into discussion.")

    for round_num in range(1, rounds + 1):
        log_info(f"Round {round_num}/{rounds}")

        # Only allow user questions after the first full round
        can_ask = allow_user_questions and round_num > 1

        # --- Claude's turn ---
        claude_prompt = _build_prompt(task, plan, history, "Claude", "Codex")
        log_info("Waiting for Claude ...")
        claude_reply = claude.query(claude_prompt)
        history.append({"agent": "Claude", "message": claude_reply})
        log_agent("Claude", claude_reply)

        # Check if Claude is asking the user something
        if can_ask and _has_user_question(claude_reply):
            answer = _ask_user("Claude", claude_reply)
            if answer:
                history.append({"agent": "User", "message": answer})
                log_agent("User", answer)

        # --- Codex's turn ---
        codex_prompt = _build_prompt(task, plan, history, "Codex", "Claude")
        log_info("Waiting for Codex ...")
        codex_reply = codex.query(codex_prompt)
        history.append({"agent": "Codex", "message": codex_reply})
        log_agent("Codex", codex_reply)

        # Check if Codex is asking the user something
        if can_ask and _has_user_question(codex_reply):
            answer = _ask_user("Codex", codex_reply)
            if answer:
                history.append({"agent": "User", "message": answer})
                log_agent("User", answer)

    return history


def _build_prompt(
    task: str,
    plan: str,
    history: list[dict[str, str]],
    current_agent: str,
    other_agent: str,
) -> str:
    lines = [
        f"You are {current_agent}, a senior technical lead (admin).",
        f"You are collaborating with {other_agent} (another admin) on this task.",
        "A separate developer will implement whatever you two agree on.",
        "You do NOT write code, create files, or delegate tasks. You may read the repo. You only discuss.",
        f"\nTASK: {task}\n",
        f"CURRENT PLAN DRAFT:\n{plan}\n",
        "You and " + other_agent + " DISAGREE on this plan. Resolve the disagreements.",
        "IMPORTANT: You have a MAXIMUM of 2 rounds. Reach a decision as fast as possible — 1 round is fine. No stalling.\n",
    ]
    if history:
        lines.append("CONVERSATION SO FAR:")
        for entry in history:
            lines.append(f"[{entry['agent']}]: {entry['message']}")
        lines.append("")

    lines.append(
        "RULES:\n"
        "- You are an ADMIN. You do NOT write code, create files, or delegate to anyone.\n"
        "- Focus on resolving disagreements about the plan.\n"
        "- Be concise. Bullet points, not essays.\n"
        "- Make decisions, don't ramble about options.\n"
        "- If you need input from the user, prefix with @User and ask directly.\n"
        "- No philosophizing. No restating the task. Just actionable output.\n"
    )
    return "\n".join(lines)
