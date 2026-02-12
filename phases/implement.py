from pathlib import Path

from agents.claude_agent import ClaudeAgent
from utils.logger import log_info, log_phase, log_success, log_error


def run_implementation(task: str, plan: str, claude: ClaudeAgent) -> str:
    """Have Claude Code implement the plan interactively.

    Writes the plan to .orchestrator/plan.md as a safety net, then launches
    Claude Code in interactive mode (full TUI). Returns a status string.
    """
    log_phase("Phase 3: Implementation")

    # Write plan to disk so Claude Code can read it directly
    plan_dir = Path(claude.working_dir) / ".orchestrator"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "plan.md"
    plan_path.write_text(
        f"# Task\n\n{task}\n\n# Implementation Plan\n\n{plan}\n",
        encoding="utf-8",
    )
    log_info(f"Plan written to {plan_path}")

    log_info("Launching Claude Code interactively ...")
    exit_code = claude.implement_interactive(task, plan)

    if exit_code == 0:
        log_success("Claude Code finished successfully.")
        return "Implementation completed interactively."
    else:
        log_error(f"Claude Code exited with code {exit_code}.")
        return f"Implementation finished with exit code {exit_code}."
