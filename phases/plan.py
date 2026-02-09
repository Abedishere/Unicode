from __future__ import annotations

import re
from pathlib import Path

from agents.base import BaseAgent
from utils.logger import log_agent, log_info, log_phase

# Patterns indicating the reviewer disagrees or wants changes
_DISAGREE = re.compile(
    r"(disagree|change|wrong|incorrect|missing|should be|instead of"
    r"|problem with|issue with|concern|reconsider|not right|flawed"
    r"|won't work|doesn't account|overlooked|forgot)",
    re.IGNORECASE,
)


def consolidate_plan(
    task: str,
    claude: BaseAgent,
    codex: BaseAgent,
    working_dir: str,
    discussion: list[dict[str, str]] | None = None,
) -> tuple[str, bool]:
    """Create an implementation plan. Codex drafts, Claude reviews.

    Returns (final_plan, agreed) where agreed=False means they disagree
    and a discussion round is needed.
    """
    log_phase("Phase 1: Plan")

    context = ""
    if discussion:
        transcript = "\n".join(
            f"[{e['agent']}]: {e['message']}" for e in discussion
        )
        context = f"\nPREVIOUS DISCUSSION:\n{transcript}\n\n"

    # Codex (GPT) drafts the plan — cheaper
    codex_prompt = (
        "You are Codex, a senior technical lead (admin). You are collaborating "
        "with Claude (another admin). A developer will implement your plan.\n"
        "You do NOT write code or create files. You may read the repo to inform your plan.\n\n"
        f"TASK: {task}\n{context}"
        "Write the implementation plan. Markdown format. Include ONLY:\n"
        "1. Files to create/modify (exact paths)\n"
        "2. Step-by-step build order\n"
        "3. Key technical decisions\n\n"
        "No preamble. No options. No 'we could do X or Y'. Just the plan."
    )
    log_info("Codex is drafting the plan ...")
    plan_draft = codex.query(codex_prompt)
    log_agent("Codex", plan_draft)

    # Claude reviews
    claude_prompt = (
        "You are Claude, a senior technical lead (admin). You are collaborating "
        "with Codex (another admin). A developer will implement this plan.\n"
        "You do NOT write code or create files. You may read the repo to inform your review.\n\n"
        f"TASK: {task}\n\n"
        f"PLAN (written by Codex for a developer to implement):\n{plan_draft}\n\n"
        "Review this plan as a technical lead — check for:\n"
        "- Missing steps or files\n"
        "- Incorrect ordering\n"
        "- Flawed design decisions\n\n"
        "If the plan is solid, say APPROVED and return it as-is.\n"
        "If you spot issues, say CHANGES_NEEDED, explain briefly what's wrong, "
        "then return the corrected plan. Do NOT rewrite into explicit instructions. "
        "Return ONLY the verdict line + the plan."
    )
    log_info("Claude is reviewing the plan ...")
    review = claude.query(claude_prompt)
    log_agent("Claude", review)

    # Determine if they agree
    agreed = not _DISAGREE.search(review)
    review_upper = review.strip().upper()
    if review_upper.startswith("APPROVED"):
        agreed = True
    elif review_upper.startswith("CHANGES_NEEDED"):
        agreed = False

    # Use Claude's version (corrected or approved-as-is)
    # Strip the verdict line if present
    final_plan = review
    for prefix in ("APPROVED", "CHANGES_NEEDED"):
        if final_plan.strip().upper().startswith(prefix):
            lines = final_plan.split("\n", 1)
            final_plan = lines[1].strip() if len(lines) > 1 else plan_draft
            break

    # If Claude only gave feedback without a full plan, keep Codex's draft
    if len(final_plan) < len(plan_draft) // 2:
        final_plan = plan_draft

    # Save internal plan
    plan_dir = Path(working_dir) / ".orchestrator"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "plan.md"
    plan_path.write_text(final_plan, encoding="utf-8")
    log_info(f"Internal plan saved to {plan_path}")

    if agreed:
        log_info("Both admins agree on the plan.")
    else:
        log_info("Admins disagree — discussion needed.")

    return final_plan, agreed
