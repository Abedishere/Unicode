from __future__ import annotations

from agents.base import BaseAgent
from utils.logger import format_transcript, log_agent, log_error, log_info, log_phase
from utils.plan_parser import is_structured, parse_plan

_PLAN_FORMAT = (
    "## Shared Dependencies\n"
    "`name`: description (used by: file1.py, file2.py)\n\n"
    "## Files\n\n"
    "### path/to/file.py (CREATE)\n"
    "- Spec for this new file\n"
    "- List every function, class, endpoint to create\n\n"
    "### path/to/file.py (MODIFY)\n"
    "- Spec for this existing file\n"
    "- List every function, class, endpoint to change\n"
)


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
        context = f"\n<discussion>\n{format_transcript(discussion)}\n</discussion>\n\n"

    skeleton = ""
    if repo_map:
        skeleton = (
            "<codebase>\n"
            f"{repo_map}\n"
            "</codebase>\n\n"
            "Use this skeleton to understand what already exists. "
            "Reference existing files accurately.\n\n"
        )

    codex_prompt = (
        f"{memory_context}"
        f"{skeleton}"
        "YOU MUST OUTPUT A STRUCTURED PLAN. "
        "The file-by-file implementation system REQUIRES the exact format shown below. "
        "A plain prose plan will BREAK THE PIPELINE — every file must have its own "
        "### header with (CREATE) or (MODIFY). There are no exceptions.\n\n"
        "<output_format>\n"
        "────────────────────────\n"
        f"{_PLAN_FORMAT}"
        "────────────────────────\n"
        "</output_format>\n\n"
        "<role>You are Codex, a senior technical lead (admin). "
        "A developer will implement your plan.\n"
        "You do NOT write code or create files. You may read the repo to inform your plan.</role>\n\n"
        f"<task>{task}</task>\n"
        f"{context}"
        "<rules>\n"
        "- MANDATORY: Use the exact structured format above — no deviations\n"
        "- Every file that needs to be created or modified MUST have its own ### section\n"
        "- The action must be CREATE (new file) or MODIFY (existing file)\n"
        "- List shared dependencies in ## Shared Dependencies before ## Files\n"
        "- Reference shared dependencies by the exact names listed above\n"
        "- Be specific: name every function, class, endpoint, model\n"
        "- No preamble. No options. No 'we could do X or Y'. Just the plan.\n"
        "</rules>"
    )
    log_info("Codex is writing the plan ...")
    plan = codex.query(codex_prompt)
    log_agent("Codex", plan)

    if not plan.strip():
        log_info("Warning: Codex returned empty plan.")

    # ── Structured-format enforcement: one retry ──────────────────────────
    if not is_structured(parse_plan(plan)):
        log_info(
            "Warning: plan is not structured (no ### file (CREATE|MODIFY) headers). "
            "Sending re-prompt to reformat …"
        )
        retry_prompt = (
            "<rules>\n"
            "The plan you wrote is not in the required structured format. "
            "The file-by-file implementation system cannot process it. "
            "Reformat the entire plan using the exact structure in <output_format>. "
            "Every file must appear as ### path/to/file.py (CREATE) or ### path/to/file.py (MODIFY). "
            "No plain prose. No preamble.\n"
            "</rules>\n\n"
            "<output_format>\n"
            "────────────────\n"
            f"{_PLAN_FORMAT}"
            "────────────────\n"
            "</output_format>\n\n"
            "<context>\n"
            "Here is the plan you wrote that needs reformatting:\n\n"
            f"{plan}\n"
            "</context>"
        )
        try:
            retry_plan = codex.query(retry_prompt)
        except Exception:
            retry_plan = ""

        if retry_plan.strip() and is_structured(parse_plan(retry_plan)):
            log_info("Re-prompt succeeded — using structured plan.")
            log_agent("Codex (retry)", retry_plan)
            plan = retry_plan
        else:
            log_error(
                "WARNING: Structured plan failed after retry — falling back to monolithic. "
                "File-by-file implementation will be skipped."
            )
            if retry_plan.strip():
                log_agent("Codex (retry, unstructured)", retry_plan)

    return plan
