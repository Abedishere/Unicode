from __future__ import annotations

from agents.base import BaseAgent
from utils.logger import format_transcript, log_agent, log_info, log_phase


def consolidate_plan(
    task: str,
    codex: BaseAgent,
    working_dir: str,
    discussion: list[dict[str, str]] | None = None,
    memory_context: str = "",
    repo_map: str = "",
) -> str:
    """Create an implementation plan. Codex writes it based on the agreed discussion.

    *memory_context* is the shared memory string from past tasks, prepended
    to the prompt so Codex can leverage prior learnings.

    *repo_map* is the compressed codebase skeleton so Codex can reference
    existing files accurately.
    """
    log_phase("Phase 2: Plan")

    context = ""
    if discussion:
        context = f"\nAGREED DISCUSSION:\n{format_transcript(discussion)}\n\n"

    skeleton = ""
    if repo_map:
        skeleton = (
            "CODEBASE SKELETON (existing project structure):\n"
            f"{repo_map}\n\n"
            "Use this skeleton to understand what already exists. "
            "Reference existing files accurately.\n\n"
        )

    codex_prompt = (
        f"{memory_context}"
        f"{skeleton}"
        "You are Codex, a senior technical lead (admin). "
        "A developer will implement your plan.\n"
        "You do NOT write code or create files. You may read the repo to inform your plan.\n\n"
        f"TASK: {task}\n{context}"
        "Write the implementation plan in this EXACT structure:\n\n"
        "## Shared Dependencies\n"
        "List every function, class, constant, or type that is used across multiple files.\n"
        "Format: `name`: description (used by: file1.py, file2.py)\n\n"
        "## Files\n\n"
        "### path/to/file.py (CREATE|MODIFY)\n"
        "- Detailed spec of what this file should contain/change\n"
        "- List specific functions, classes, endpoints to create/modify\n"
        "- Reference shared dependencies by name\n\n"
        "RULES:\n"
        "- Every file that needs to be created or modified MUST have its own ### section\n"
        "- The action must be CREATE (new file) or MODIFY (existing file)\n"
        "- Reference shared dependencies by the exact names listed above\n"
        "- Be specific: name every function, class, endpoint, model\n"
        "- No preamble. No options. No 'we could do X or Y'. Just the plan."
    )
    log_info("Codex is writing the plan ...")
    plan = codex.query(codex_prompt)
    log_agent("Codex", plan)

    if not plan.strip():
        log_info("Warning: Codex returned empty plan.")

    return plan
