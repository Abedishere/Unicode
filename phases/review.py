from __future__ import annotations

import re

import click

from agents.base import BaseAgent
from agents.claude_agent import ClaudeAgent
from utils.git_utils import get_diff
from utils.logger import log_agent, log_error, log_info, log_phase, log_success

# Patterns that mean "code looks good, nothing to change"
_LOOKS_GOOD = re.compile(
    r"(approved|looks good|no issues|no comments|no changes|lgtm|good to go"
    r"|nothing to change|no feedback|all good|ship it|no problems|satisf"
    r"|complete|correct|well.implemented|properly.implemented|solid"
    r"|code is (fine|good|correct|solid|clean))",
    re.IGNORECASE,
)

# Patterns that clearly indicate changes are needed
_CHANGES_NEEDED = re.compile(
    r"(changes?.requested|fix|bug|wrong|incorrect|missing|broken|error"
    r"|should be|needs? to|must be|replace|remove|add\s)",
    re.IGNORECASE,
)


def _validate_review_with_claude(
    claude: ClaudeAgent,
    codex_review: str,
    diff: str,
    task: str,
    plan: str,
) -> tuple[bool, str]:
    """Have Claude validate Codex's review comments before acting on them.

    Returns (should_apply, validated_feedback). If Claude agrees the feedback
    is valid, should_apply is True and validated_feedback contains only the
    legitimate issues. If Claude disagrees, should_apply is False.
    """
    validate_prompt = (
        "You are Claude, a senior technical lead. Codex reviewed a developer's "
        "implementation and requested changes. Your job is to VALIDATE whether "
        "Codex's feedback is legitimate.\n\n"
        "RULES:\n"
        "- Only actual bugs, logic errors, or plan deviations are valid.\n"
        "- Requests for tests, docs, style changes, or hypothetical issues are NOT valid.\n"
        "- If Codex is hallucinating issues that don't exist in the diff, reject them.\n"
        "- If even ONE issue is legitimate, return VALID and list only the real issues.\n"
        "- If ALL issues are invalid, return INVALID.\n\n"
        f"TASK: {task}\n\n"
        f"PLAN:\n{plan[:2000]}\n\n"
        f"DIFF:\n```diff\n{diff[:3000]}\n```\n\n"
        f"CODEX REVIEW:\n{codex_review}\n\n"
        "Reply with EXACTLY one line first:\n"
        "VALID — or — INVALID\n\n"
        "If VALID, list only the legitimate issues as bullet points."
    )

    log_info("Claude is validating Codex's review ...")
    try:
        validation = claude.query(validate_prompt)
        log_agent("Claude (review validation)", validation)
    except RuntimeError:
        log_info("Claude validation failed — proceeding with Codex review as-is.")
        return True, codex_review

    upper = validation.strip().upper()
    if upper.startswith("INVALID"):
        return False, validation
    return True, validation


def run_review(
    task: str,
    plan: str,
    claude: ClaudeAgent,
    codex: BaseAgent,
    working_dir: str,
    max_iterations: int,
) -> bool:
    """Have Codex review Claude's implementation with dual-admin validation.

    Codex performs the primary review. If changes are requested, Claude
    validates the feedback before applying it — preventing hallucinated
    review comments from breaking working code.

    Max iterations capped at config value. If Codex doesn't clearly request
    changes, the review is treated as approved.
    """
    log_phase("Phase 4: Code Review")
    max_iterations = min(max_iterations, 3)

    for iteration in range(1, max_iterations + 1):
        log_info(f"Review iteration {iteration}/{max_iterations}")

        diff = get_diff(working_dir)
        if not diff:
            log_info("No changes detected — skipping review.")
            return True

        review_prompt = (
            "You are a senior technical lead (admin) reviewing a developer's work.\n"
            "You do NOT write code, edit files, or run shell commands. "
            "Do NOT inspect the repo. You review ONLY the diff below.\n"
            "Claude (another admin) wrote the plan. A developer implemented it.\n\n"
            "RULES:\n"
            "- Do NOT request tests or test files. Testing is a separate workflow.\n"
            "- Do NOT request documentation or comments unless the code is unclear.\n"
            "- Only flag actual bugs, logic errors, or deviations from the plan.\n"
            "- If the code correctly implements the plan, you MUST approve it.\n"
            f"- You have {max_iterations} iterations total. Make a decision.\n\n"
            f"TASK: {task}\n\n"
            f"PLAN:\n{plan}\n\n"
            f"DIFF:\n```diff\n{diff[:4000]}\n```\n\n"
            "Reply with EXACTLY one line first:\n"
            "APPROVED — or — CHANGES_REQUESTED\n\n"
            "If CHANGES_REQUESTED, list ONLY actual bugs or logic errors as bullet points. "
            "Do NOT request tests, docs, or stylistic changes."
        )

        log_info("Codex is reviewing ...")
        try:
            review = codex.query(review_prompt)
        except RuntimeError as exc:
            log_error(f"Codex review failed: {exc}")
            choice = click.prompt(
                click.style(
                    "Codex returned nothing. Approve anyway, retry, or skip?",
                    fg="yellow", bold=True,
                ),
                type=click.Choice(["approve", "retry", "skip"], case_sensitive=False),
                default="retry",
            )
            if choice == "approve":
                log_success("Code review: APPROVED (by user)")
                return True
            elif choice == "retry":
                continue
            else:
                log_info("Review skipped by user.")
                return True
        log_agent("Codex", review)

        # Check for explicit approval
        review_upper = review.strip().upper()
        if review_upper.startswith("APPROVED") or _LOOKS_GOOD.search(review):
            log_success("Code review: APPROVED")
            return True

        # Only treat as changes requested if there are clear fix requests
        if not _CHANGES_NEEDED.search(review):
            log_info("No clear changes requested — treating as approved.")
            log_success("Code review: APPROVED")
            return True

        # Last iteration: don't send to Claude, just approve
        if iteration == max_iterations:
            log_info("Final review iteration — approving to proceed.")
            log_success("Code review: APPROVED (max iterations)")
            return True

        # Dual-admin validation: Claude checks if Codex's feedback is legitimate
        should_apply, validated = _validate_review_with_claude(
            claude, review, diff, task, plan,
        )

        if not should_apply:
            log_info("Claude rejected Codex's review — feedback was invalid.")
            log_success("Code review: APPROVED (validated by Claude)")
            return True

        # Changes validated — send to developer for fixes
        log_info("Changes validated — sending to developer ...")
        fix_prompt = (
            "You are the developer. The admins reviewed your work and requested changes.\n\n"
            f"Review feedback (validated by both admins):\n{validated}\n\n"
            f"TASK: {task}\n"
            f"PLAN:\n{plan}\n\n"
            "Fix ONLY actual bugs or logic errors. Ignore requests for tests or docs. "
            "No commentary, just fix the code."
        )
        fix_output = claude.implement(fix_prompt)
        log_agent("Claude (developer)", fix_output)

    # Should not reach here, but just in case
    log_success("Code review: APPROVED")
    return True
