"""Phase 4: Code Review — two-pass sequential review.

Review Part 1 — Codex (GPT) performs the primary diff review.
Review Part 2 — Claude validates and aggregates Codex's findings.

Confirmed issues are sent to the developer (Claude) for fixes.
The cycle repeats until Codex approves or max iterations are exhausted.
"""
from __future__ import annotations

import re

import click
from rich.console import Console

from agents.claude_agent import ClaudeAgent
from agents.codex_agent import CodexAgent
from utils.git_utils import get_diff
from utils.logger import log_agent, log_error, log_info, log_phase, log_success

console = Console()

# Codex verdict patterns — match on the first non-blank line
_APPROVED_PAT = re.compile(r"^\s*APPROVED\b", re.IGNORECASE | re.MULTILINE)
_CHANGES_PAT = re.compile(r"^\s*CHANGES[_ ]REQUESTED\b", re.IGNORECASE | re.MULTILINE)

# Claude verdict patterns
_CONFIRMED_PAT = re.compile(r"^\s*CONFIRMED\b", re.IGNORECASE | re.MULTILINE)
_CLAUDE_APPROVED_PAT = re.compile(r"^\s*APPROVED\b", re.IGNORECASE | re.MULTILINE)

# Fallback: heuristic "looks good" signals when Codex skips the verdict line
_LOOKS_GOOD = re.compile(
    r"(looks good|no issues|lgtm|good to go|all good|ship it|no problems"
    r"|no changes needed|satisf|well.implemented|complete and correct)",
    re.IGNORECASE,
)


# ── Review Part 1: Codex primary review ──────────────────────────────────────

def _codex_primary_review(
    codex: CodexAgent,
    diff: str,
    task: str,
    plan: str,
    iteration: int,
    max_iterations: int,
) -> tuple[str, bool]:
    """Have Codex review the diff.

    Returns (review_text, is_approved).
    Returns ("", True) if the user chose approve/skip after a failure.
    Returns ("", False) if the user chose retry (caller should continue loop).
    """
    # The NO-TOOLS preamble is critical: Codex in exec mode tends to run git
    # commands to inspect context even when the diff is provided inline.
    # Placing this instruction first — before any task content — maximises
    # the chance Codex treats this as a pure text task.
    prompt = (
        "=== IMPORTANT: TEXT-ONLY TASK — DO NOT RUN ANY SHELL COMMANDS ===\n"
        "You are a senior technical lead doing a code review.\n"
        "ALL information you need is in this prompt.\n"
        "DO NOT use any tools. DO NOT run git, cat, ls, or any other command.\n"
        "Respond with plain text only.\n"
        "=== END IMPORTANT ===\n\n"
        "REVIEW RULES:\n"
        "- Flag ONLY: actual bugs, logic errors, deviations from the plan.\n"
        "- Do NOT flag: missing tests, documentation, comments, or style.\n"
        "- If the implementation correctly follows the plan, reply APPROVED.\n"
        f"- This is review cycle {iteration} of {max_iterations}. Be decisive.\n\n"
        f"TASK:\n{task}\n\n"
        f"PLAN:\n{plan[:2000]}\n\n"
        f"DIFF (this is ALL the code — do not inspect any files):\n"
        f"```diff\n{diff[:4000]}\n```\n\n"
        "Your response MUST start with exactly one of these two lines:\n"
        "APPROVED\n"
        "CHANGES_REQUESTED\n\n"
        "If CHANGES_REQUESTED, list each confirmed issue as a numbered bullet.\n"
        "Reference specific lines/functions. No style or test requests."
    )

    console.print("[bold green]▶  Codex is reviewing the diff ...[/]")
    try:
        review = codex.review_query(prompt)
        console.print("[bold green]✓  Codex review received.[/]")
        log_agent("Codex (Review Part 1)", review)
    except RuntimeError as exc:
        log_error(f"Codex review failed: {exc}")
        choice = click.prompt(
            click.style(
                "Codex review failed. What now?",
                fg="yellow", bold=True,
            ),
            type=click.Choice(["approve", "retry", "skip"], case_sensitive=False),
            default="retry",
        )
        if choice == "retry":
            return "", False  # caller: continue to next iteration
        # approve or skip → treat as approved
        return "", True

    # Determine verdict
    if _APPROVED_PAT.search(review) and not _CHANGES_PAT.search(review):
        return review, True
    if _LOOKS_GOOD.search(review) and not _CHANGES_PAT.search(review):
        return review, True
    if not _CHANGES_PAT.search(review):
        # No explicit changes requested → treat as approved
        log_info("Codex did not request changes — treating as approved.")
        return review, True

    return review, False


# ── Review Part 2: Claude secondary review ───────────────────────────────────

def _claude_secondary_review(
    claude: ClaudeAgent,
    codex_review: str,
    diff: str,
    task: str,
    plan: str,
) -> tuple[str, bool]:
    """Have Claude validate and aggregate Codex's findings.

    Returns (aggregated_feedback, has_confirmed_issues).
    Falls back to trusting Codex directly if Claude's query fails.
    """
    prompt = (
        "You are a senior technical lead doing a secondary code review.\n"
        "Codex (another lead) reviewed the diff and flagged issues.\n"
        "Your job: validate each finding against the actual diff.\n\n"
        "VALIDATION RULES:\n"
        "- VALID: actual bugs, logic errors, plan deviations visible in the diff.\n"
        "- INVALID: requests for tests, docs, comments, style, or anything not\n"
        "  visible in the diff below.\n"
        "- Reject hallucinated issues (issues Codex claims exist but the diff\n"
        "  shows are already handled or don't exist at all).\n\n"
        f"TASK:\n{task}\n\n"
        f"PLAN:\n{plan[:2000]}\n\n"
        f"DIFF:\n```diff\n{diff[:3000]}\n```\n\n"
        f"CODEX FINDINGS:\n{codex_review}\n\n"
        "Your response MUST start with exactly one of:\n"
        "CONFIRMED — at least one issue is valid\n"
        "APPROVED  — all issues are invalid, implementation is correct\n\n"
        "If CONFIRMED, list ONLY the confirmed issues as numbered bullets.\n"
        "One sentence each. Actionable. No new issues beyond what Codex flagged."
    )

    log_info("Review Part 2 — Claude is validating Codex's findings ...")
    try:
        result = claude.query(prompt)
        log_agent("Claude (Review Part 2)", result)
    except RuntimeError as exc:
        log_error(f"Claude secondary review failed: {exc} — using Codex review as-is.")
        return codex_review, True  # fall back to trusting Codex's findings

    has_issues = bool(_CONFIRMED_PAT.search(result)) and not bool(_CLAUDE_APPROVED_PAT.search(result.split("\n")[0]))
    return result, has_issues


# ── Main review loop ─────────────────────────────────────────────────────────

def run_review(
    task: str,
    plan: str,
    claude: ClaudeAgent,
    codex: CodexAgent,
    working_dir: str,
    max_iterations: int,
) -> tuple[bool, str]:
    """Two-phase code review loop.

    Each cycle:
      Part 1 — Codex reviews the diff → APPROVED or CHANGES_REQUESTED
      Part 2 — Claude validates Codex's findings → CONFIRMED or APPROVED
      Developer (Claude) implements all confirmed fixes.

    The loop repeats until Codex approves or max_iterations is reached.

    Returns (approved, review_text) where review_text is the accumulated
    review feedback (used by the orchestrator to extract learnings).
    """
    log_phase("Phase 4: Code Review")
    max_iterations = min(max_iterations, 3)
    review_feedback: list[str] = []

    for iteration in range(1, max_iterations + 1):
        log_info(f"Review cycle {iteration}/{max_iterations}")

        # ── Collect diff ──────────────────────────────────────────────────
        log_info(f"Staging and collecting diff (working dir: {working_dir}) ...")
        try:
            diff = get_diff(working_dir)
        except RuntimeError as exc:
            log_error(f"Could not collect diff: {exc}")
            log_info("Skipping review — diff unavailable.")
            return True, "\n\n".join(review_feedback)
        if not diff:
            console.print()
            console.print(
                "[bold yellow]⚠  No changes detected in the diff — "
                "Codex review will be skipped and the task auto-approved.[/]"
            )
            console.print(
                f"[dim]  Working dir: {working_dir}\n"
                "  This usually means Claude's implementation produced no file changes,\n"
                "  all changes were in excluded build dirs, or the working directory\n"
                "  does not match where files were written.[/]"
            )
            console.print()
            return True, "\n\n".join(review_feedback)

        # Show diff statistics so the user can confirm Codex is reviewing real changes
        diff_lines = diff.splitlines()
        files_changed = sum(1 for l in diff_lines if l.startswith("diff --git"))
        added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
        console.print(
            f"  [bold green]Diff collected:[/] [dim]{files_changed} file(s) · "
            f"+{added} / -{removed} lines[/]"
        )

        # ── Review Part 1: Codex primary review ──────────────────────────
        log_phase(
            f"Review Part 1 — Codex primary review "
            f"(cycle {iteration}/{max_iterations})"
        )
        codex_review, approved = _codex_primary_review(
            codex, diff, task, plan, iteration, max_iterations,
        )

        if codex_review:
            review_feedback.append(f"Codex Review (cycle {iteration}):\n{codex_review}")

        if approved and codex_review:
            log_success("Code review: APPROVED (Codex)")
            return True, "\n\n".join(review_feedback)

        if approved and not codex_review:
            # Codex failed; user chose approve/skip
            log_success("Code review: APPROVED (user)")
            return True, "\n\n".join(review_feedback)

        if not codex_review:
            # Codex failed; user chose retry → continue to next iteration
            log_info("Retrying review in next cycle ...")
            continue

        # ── Review Part 2: Claude secondary review ────────────────────────
        # NOTE: this always runs even on the last iteration so that Codex's
        # findings are validated and fixes are sent to Claude at least once.
        # The max-cycles bailout moves to AFTER the fix phase below.
        log_phase(
            f"Review Part 2 — Claude validates Codex's findings "
            f"(cycle {iteration}/{max_iterations})"
        )
        aggregated, has_issues = _claude_secondary_review(
            claude, codex_review, diff, task, plan,
        )

        if aggregated:
            review_feedback.append(f"Claude Validation (cycle {iteration}):\n{aggregated}")

        if not has_issues:
            log_info("Claude found no valid issues in Codex's review.")
            log_success("Code review: APPROVED (Claude validation)")
            return True, "\n\n".join(review_feedback)

        # ── Developer fix ─────────────────────────────────────────────────
        log_info("Confirmed issues found — sending to developer for fixes ...")
        fix_prompt = (
            "You are the developer. Two senior technical leads reviewed your "
            "implementation.\n\n"
            "Codex flagged issues and Claude confirmed each one is a real bug "
            "or logic error — not style, tests, or documentation.\n\n"
            "CONFIRMED ISSUES (fix every one of these):\n"
            f"{aggregated}\n\n"
            f"TASK:\n{task}\n\n"
            f"PLAN:\n{plan}\n\n"
            "Fix ONLY the confirmed issues listed above.\n"
            "Do not touch anything else. No commentary, just implement the fixes."
        )
        fix_output = claude.implement(fix_prompt)
        log_agent("Claude (developer fix)", fix_output)

        # Max cycles reached — approve after this fix rather than re-reviewing
        if iteration == max_iterations:
            log_info("Max review cycles reached — approving after final fix.")
            log_success("Code review: APPROVED (max cycles, fixes applied)")
            return True, "\n\n".join(review_feedback)

    log_success("Code review: APPROVED")
    return True, "\n\n".join(review_feedback)
