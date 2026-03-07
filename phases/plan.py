from __future__ import annotations

from pathlib import Path

from agents.base import BaseAgent
from utils.logger import format_transcript, log_agent, log_info, log_phase


def consolidate_plan(
    task: str,
    codex: BaseAgent,
    working_dir: str,
    discussion: list[dict[str, str]] | None = None,
    memory_context: str = "",
) -> str:
    """Create an implementation plan. Codex writes it based on the agreed discussion.

    *memory_context* is the shared memory string from past tasks, prepended
    to the prompt so Codex can leverage prior learnings.
    """
    log_phase("Phase 2: Plan")

    context = ""
    if discussion:
        context = f"\nAGREED DISCUSSION:\n{format_transcript(discussion)}\n\n"

    codex_prompt = (
        f"{memory_context}"
        "You are Codex, a senior technical lead (admin). "
        "A developer will implement your plan.\n"
        "You do NOT write code or create files. You may read the repo to inform your plan.\n\n"
        f"TASK: {task}\n{context}"
        "Write the implementation plan based on the agreed discussion above. "
        "Markdown format. Include ONLY:\n"
        "1. Files to create/modify (exact paths)\n"
        "2. Step-by-step build order\n"
        "3. Key technical decisions\n\n"
        "No preamble. No options. No 'we could do X or Y'. Just the plan."
    )
    log_info("Codex is writing the plan ...")
    plan = codex.query(codex_prompt)
    log_agent("Codex", plan)

    plan_dir = Path(working_dir) / ".orchestrator"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "plan.md"
    plan_path.write_text(plan, encoding="utf-8")
    log_info(f"Plan saved to {plan_path}")

    return plan
