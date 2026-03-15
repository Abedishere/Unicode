from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agents.claude_agent import ClaudeAgent
from utils.logger import log_info, log_phase, log_success

if TYPE_CHECKING:
    from utils.plan_parser import StructuredPlan


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


def _implement_file_by_file(
    task: str,
    structured_plan: StructuredPlan,
    claude: ClaudeAgent,
    repo_map: str = "",
    memory_context: str = "",
) -> str:
    """Implement the plan one file at a time.

    Each file gets a focused prompt with only the repo skeleton, shared
    dependencies, and that file's specific spec.  This produces more
    reliable output for larger projects since each call has a smaller,
    focused context.
    """
    total = len(structured_plan.files)
    results = []

    skeleton = ""
    if repo_map:
        skeleton = f"CODEBASE SKELETON:\n{repo_map}\n\n"

    shared_deps = ""
    if structured_plan.shared_dependencies:
        shared_deps = (
            "SHARED DEPENDENCIES (used across files — reference these exactly):\n"
            f"{structured_plan.shared_dependencies}\n\n"
        )

    for i, file_spec in enumerate(structured_plan.files, 1):
        log_info(f"Implementing file {i}/{total}: {file_spec.path}")

        action_hint = (
            "Create this file from scratch."
            if file_spec.action == "CREATE"
            else "Modify this existing file."
        )

        prompt = (
            f"{memory_context}"
            f"TASK SUMMARY: {task[:500]}\n\n"
            f"{skeleton}"
            f"{shared_deps}"
            f"YOUR ASSIGNMENT — {file_spec.action} {file_spec.path}:\n"
            f"{file_spec.spec}\n\n"
            f"RULES:\n"
            f"- Implement ONLY this file: {file_spec.path}\n"
            f"- {action_hint}\n"
            "- Use the shared dependency names exactly as listed above.\n"
            "- Follow the spec precisely. No extras.\n\n"
            "IMPORTANT: When creating or modifying requirements.txt or pyproject.toml, "
            "always pin package versions with a minimum version constraint "
            "(e.g. `click>=8.1.0`, not just `click`)."
        )

        result = claude.implement(prompt)
        results.append(f"[{file_spec.path}] done")
        log_success(f"  {file_spec.path} — done")

    return f"File-by-file implementation complete ({total} files):\n" + "\n".join(results)


def run_implementation(
    task: str,
    plan: str,
    claude: ClaudeAgent,
    discussion: list[dict[str, str]] | None = None,
    memory_context: str = "",
    repo_map: str = "",
    structured_plan: StructuredPlan | None = None,
) -> str:
    """Have Claude Code implement the plan non-interactively.

    Writes the plan to .orchestrator/plan.md as a safety net, then runs
    Claude Code in print mode (non-interactive) so the pipeline can continue.

    If *structured_plan* is provided and successfully parsed into file specs,
    uses file-by-file generation.  Otherwise falls back to monolithic
    implementation.

    *repo_map* is the compressed codebase skeleton for context.

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

    # Decide strategy: file-by-file or monolithic
    from utils.plan_parser import is_structured
    if structured_plan and is_structured(structured_plan):
        log_info(f"Using file-by-file generation ({len(structured_plan.files)} files)")
        return _implement_file_by_file(
            task, structured_plan, claude, repo_map, memory_context,
        )

    # Monolithic fallback
    log_info("Using monolithic implementation (unstructured plan)")
    context_brief = _build_context_brief(discussion)

    skeleton = ""
    if repo_map:
        skeleton = f"CODEBASE SKELETON:\n{repo_map}\n\n"

    log_info(f"Running Claude Code (dev:{claude.dev_model}) ...")
    implement_prompt = (
        f"{memory_context}"
        f"{skeleton}"
        f"{context_brief}"
        f"TASK:\n{task}\n\n"
        f"IMPLEMENTATION PLAN:\n{plan}\n\n"
        "Implement the plan exactly. Follow every step.\n\n"
        "IMPORTANT: When creating or modifying requirements.txt or pyproject.toml, "
        "always pin package versions with a minimum version constraint "
        "(e.g. `click>=8.1.0`, not just `click`). "
        "Look up the current stable version of each package and use it as the lower bound."
    )
    result = claude.implement(implement_prompt)
    log_success("Claude Code finished implementation.")
    return result
