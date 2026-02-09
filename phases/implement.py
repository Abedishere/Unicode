from agents.claude_agent import ClaudeAgent
from utils.logger import log_agent, log_info, log_phase


def run_implementation(task: str, plan: str, claude: ClaudeAgent) -> str:
    """Have Claude Code implement the plan using its tools.

    Returns Claude's implementation output.
    """
    log_phase("Phase 3: Implementation")

    prompt = (
        "You are the developer. Two senior admins (Claude and Codex) discussed and "
        "wrote this plan for you. Implement it exactly.\n\n"
        f"TASK: {task}\n\n"
        f"IMPLEMENTATION PLAN:\n{plan}\n\n"
        "Create all necessary files, write all code, "
        "and ensure everything is complete and working. Follow the plan exactly."
    )
    log_info("Waiting for Claude to implement the plan ...")
    output = claude.implement(prompt)
    log_agent("Claude", output)
    return output
