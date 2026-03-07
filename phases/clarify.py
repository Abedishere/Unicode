"""Phase 0: Interpreter — chat with the user to fully understand the task,
then compile a brief for the agents. If agents have questions later,
they come back through here."""

from __future__ import annotations

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agents.base import BaseAgent
from utils.logger import log_info, log_phase

console = Console()

INTERPRETER_SYSTEM = (
    "You are the Interpreter for an AI orchestrator. Your job is to have a "
    "short, focused conversation with the user to fully understand what they "
    "want built. Ask clarifying questions one or two at a time. Be concise "
    "and friendly. When you have enough information, say READY and summarize "
    "the task as a clear brief.\n\n"
    "Rules:\n"
    "- Don't be annoying — if the task is already clear, just say READY\n"
    "- At most 3-4 exchanges, then move on\n"
    "- If the user says 'go', 'done', 'just do it', etc. → say READY immediately\n"
    "- Your READY summary should be a clean task brief the dev team can work from\n"
)


def _collect_input() -> str:
    """Collect a single user message."""
    try:
        return click.prompt(
            click.style("You", fg="white", bold=True),
            default="",
            show_default=False,
        )
    except (EOFError, click.Abort):
        return ""


def run_interpreter(task: str, interpreter: BaseAgent) -> str:
    """Chat with the user via the interpreter (Qwen) to refine the task.

    Returns a clean task brief string.
    """
    log_phase("Phase 0: Interpreter")
    console.print("[dim]The interpreter will help clarify your task before "
                  "the agents start working.[/]")
    console.print("[dim]Type 'go' or 'done' at any time to skip ahead.[/]")
    console.print()

    history: list[dict[str, str]] = []

    # First interpreter turn — analyze the task
    first_prompt = (
        f"{INTERPRETER_SYSTEM}\n"
        f"The user's initial request:\n\"{task}\"\n\n"
        "Analyze this. If it's clear enough to work on, reply with READY followed "
        "by your task brief. Otherwise, ask your first clarifying question(s)."
    )
    reply = interpreter.query(first_prompt)
    history.append({"role": "assistant", "content": reply})

    # Check if interpreter says it's ready immediately
    if "READY" in reply:
        brief = reply.split("READY", 1)[1].strip()
        if not brief:
            brief = task
        console.print(Panel(
            Text(brief),
            title="[bold green]Task Brief[/]",
            border_style="green",
        ))
        log_info("Interpreter: task is clear, moving to agents.")
        return brief

    # Show interpreter's questions
    console.print(Panel(
        Text(reply),
        title="[bold magenta]Interpreter[/]",
        border_style="magenta",
    ))

    # Conversation loop (max 4 turns)
    for turn in range(4):
        console.print()
        user_input = _collect_input()

        if not user_input.strip():
            break

        # Quick exit keywords
        if user_input.strip().lower() in ("go", "done", "just do it", "skip", "proceed"):
            break

        history.append({"role": "user", "content": user_input})

        # Build follow-up prompt
        conv_text = "\n".join(
            f"{'Interpreter' if h['role'] == 'assistant' else 'User'}: {h['content']}"
            for h in history
        )
        follow_prompt = (
            f"{INTERPRETER_SYSTEM}\n"
            f"Original request: \"{task}\"\n\n"
            f"Conversation so far:\n{conv_text}\n\n"
            "Continue the conversation. If you now have enough info, "
            "reply with READY followed by your complete task brief."
        )
        reply = interpreter.query(follow_prompt)
        history.append({"role": "assistant", "content": reply})

        if "READY" in reply:
            brief = reply.split("READY", 1)[1].strip()
            if not brief:
                brief = task
            console.print(Panel(
                Text(brief),
                title="[bold green]Task Brief[/]",
                border_style="green",
            ))
            log_info("Interpreter: task clarified, moving to agents.")
            return brief

        console.print(Panel(
            Text(reply),
            title="[bold magenta]Interpreter[/]",
            border_style="magenta",
        ))

    # If we ran out of turns, compile a brief from what we have
    log_info("Compiling task brief from conversation ...")
    conv_text = "\n".join(
        f"{'Interpreter' if h['role'] == 'assistant' else 'User'}: {h['content']}"
        for h in history
    )
    compile_prompt = (
        f"Original request: \"{task}\"\n\n"
        f"Clarification conversation:\n{conv_text}\n\n"
        "Now compile a clear, complete task brief that incorporates everything "
        "discussed. Return ONLY the brief, no preamble."
    )
    brief = interpreter.query(compile_prompt)
    console.print(Panel(
        Text(brief),
        title="[bold green]Task Brief[/]",
        border_style="green",
    ))
    return brief


def relay_agent_questions(
    questions: str,
    interpreter: BaseAgent,
    original_task: str,
) -> str:
    """When agents have questions, relay them to the user through the interpreter.

    Returns the user's answers as a string.
    """
    console.print()
    console.print(Panel(
        Text(f"The agents have some questions:\n\n{questions}"),
        title="[bold yellow]Questions from agents[/]",
        border_style="yellow",
    ))
    console.print()
    console.print("[dim]Answer below. Press Enter twice to finish.[/]")
    console.print()

    lines: list[str] = []
    try:
        while True:
            line = _collect_input()
            if line == "" and (not lines or lines[-1] == ""):
                break
            lines.append(line)
    except (EOFError, click.Abort):
        pass

    answers = "\n".join(lines).strip()
    if not answers:
        return "No additional input — proceed with your best judgment."
    return answers
