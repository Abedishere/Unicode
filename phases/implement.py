from pathlib import Path

from agents.claude_agent import ClaudeAgent
from utils.logger import log_info, log_phase, log_success


def run_implementation(task: str, plan: str, claude: ClaudeAgent) -> str:
    """Have Claude Code implement the plan non-interactively.

    Writes the plan to .orchestrator/plan.md as a safety net, then runs
    Claude Code in print mode (non-interactive) so the pipeline can continue.
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

    log_info("Running Claude Code (developer) ...")
    implement_prompt = (
        f"TASK:\n{task}\n\n"
        f"IMPLEMENTATION PLAN:\n{plan}\n\n"
        "Implement the plan exactly. Follow every step."
    )
    result = claude.implement(implement_prompt)
    log_success("Claude Code finished implementation.")
    return result
