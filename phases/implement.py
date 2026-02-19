from __future__ import annotations

from pathlib import Path

from agents.claude_agent import ClaudeAgent
from utils.logger import log_info, log_phase, log_success


def _build_context_brief(discussion: list[dict[str, str]] | None) -> str:
    """Summarize the admin discussion into a concise context brief for the developer.

    Extracts key decisions, rejected approaches, and important notes so the
    developer doesn't repeat mistakes the admins already discussed.
    """
    if not discussion:
        return ""

    lines = []
    for entry in discussion:
        agent = entry.get("agent", "")
        msg = entry.get("message", "")
        # Keep it concise — first 300 chars of each message
        lines.append(f"[{agent}]: {msg[:300]}")

    return (
        "CONTEXT BRIEF (from admin discussion — key decisions & rejected approaches):\n"
        + "\n".join(lines)
        + "\n\n"
    )


def run_implementation(
    task: str,
    plan: str,
    claude: ClaudeAgent,
    discussion: list[dict[str, str]] | None = None,
    memory_context: str = "",
) -> str:
    """Have Claude Code implement the plan non-interactively.

    Writes the plan to .orchestrator/plan.md as a safety net, then runs
    Claude Code in print mode (non-interactive) so the pipeline can continue.

    *discussion* is the admin discussion history — summarized into a context
    brief so the developer knows what was decided and what to avoid.

    *memory_context* is the shared memory string from past tasks.

    Returns a status string.
    """
    log_phase("Phase 3: Implementation")

    # Write plan to disk as a safety net
    plan_dir = Path(claude.working_dir) / ".orchestrator"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "plan.md"
    plan_path.write_text(
        f"# Task\n\n{task}\n\n# Implementation Plan\n\n{plan}\n",
        encoding="utf-8",
    )
    log_info(f"Plan written to {plan_path}")

    context_brief = _build_context_brief(discussion)

    log_info(f"Running Claude Code (dev:{claude.dev_model}) ...")
    implement_prompt = (
        f"{memory_context}"
        f"{context_brief}"
        f"TASK:\n{task}\n\n"
        f"IMPLEMENTATION PLAN:\n{plan}\n\n"
        "Implement the plan exactly. Follow every step."
    )
    result = claude.implement(implement_prompt)
    log_success("Claude Code finished implementation.")
    return result
