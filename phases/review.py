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

# Fallback: heuristic "looks good" signals when Codex skips the verdict line
_LOOKS_GOOD = re.compile(
    r"(looks good|no issues|lgtm|good to go|all good|ship it|no problems"
    r"|no changes needed|satisf|well.implemented|complete and correct)",
    re.IGNORECASE,
)

# Reviewer requests full diff for specific files
_NEED_FULL_DIFF = re.compile(r"NEED_FULL_DIFF:\s*(.+)", re.IGNORECASE)


# ── Diff summarization ──────────────────────────────────────────────────────

_CODE_DEF = re.compile(
    r"^[+-]\s*(?:def |class |function |const |let |var |export )\s*(\w+)",
)


def _summarize_diff(diff: str) -> str:
    """Parse a git diff into a structured file-level summary.

    Returns a compact summary showing files changed, lines added/removed,
    and key structural changes (functions/classes added/removed/modified).
    """
    if not diff:
        return ""

    sections = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    file_summaries = []

    for section in sections:
        if not section.strip():
            continue

        # Extract filename
        header = re.match(r"diff --git a/(.+?) b/(.+?)$", section, re.MULTILINE)
        if not header:
            continue
        filename = header.group(2)

        # Count changes
        added = removed = 0
        added_names: list[str] = []
        removed_names: list[str] = []
        for line in section.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
                m = _CODE_DEF.match(line)
                if m:
                    added_names.append(m.group(1))
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
                m = _CODE_DEF.match(line)
                if m:
                    removed_names.append(m.group(1))

        # Classify names as added, removed, or modified
        added_set = set(added_names)
        removed_set = set(removed_names)
        modified = added_set & removed_set
        only_added = added_set - modified
        only_removed = removed_set - modified

        lines = [f"{filename} (+{added} -{removed}):"]
        for name in sorted(only_added):
            lines.append(f"  Added: {name}")
        for name in sorted(modified):
            lines.append(f"  Modified: {name}")
        for name in sorted(only_removed):
            lines.append(f"  Removed: {name}")
        if not (only_added or modified or only_removed) and (added or removed):
            lines.append("  (configuration/data changes)")

        file_summaries.append("\n".join(lines))

    total_files = len(file_summaries)
    return f"FILES CHANGED: {total_files}\n\n" + "\n\n".join(file_summaries)


def _extract_file_diff(full_diff: str, filenames: list[str]) -> str:
    """Extract diff hunks for specific files from the full diff."""
    sections = re.split(r"(?=^diff --git )", full_diff, flags=re.MULTILINE)
    matched = []
    for section in sections:
        for fname in filenames:
            if fname in section:
                matched.append(section.strip())
                break
    return "\n\n".join(matched) if matched else ""


def _handle_full_diff_request(
    review: str,
    full_diff: str,
    agent_query_fn,
    agent_label: str,
    *,
    text_only_preamble: bool = False,
) -> str:
    """If *review* contains NEED_FULL_DIFF requests, fetch the diffs and re-query.

    Returns the updated review string, or the original if no request was found
    or the follow-up fails.
    """
    full_diff_matches = _NEED_FULL_DIFF.findall(review)
    if not full_diff_matches:
        return review

    requested_files = []
    for match in full_diff_matches:
        requested_files.extend(f.strip() for f in match.split(","))
    log_info(f"{agent_label} requested full diff for: {', '.join(requested_files)}")

    extracted = _extract_file_diff(full_diff, requested_files)
    if not extracted:
        return review

    preamble = (
        "=== IMPORTANT: TEXT-ONLY TASK — DO NOT RUN ANY SHELL COMMANDS ===\n"
        "You previously reviewed a diff summary and requested full diffs.\n"
        "=== END IMPORTANT ===\n\n"
    ) if text_only_preamble else ""

    followup = (
        f"{preamble}"
        "<context>\n"
        f"Here are the full diffs you requested:\n\n"
        f"```diff\n{extracted[:6000]}\n```\n\n"
        f"Your original review so far:\n{review}\n"
        "</context>\n\n"
        "Now finalize your review with the full context. Same rules apply.\n"
        "Your response MUST start with APPROVED or CHANGES_REQUESTED."
    )
    try:
        updated = agent_query_fn(followup)
        log_agent(agent_label, updated)
        return updated
    except RuntimeError:
        log_info(f"{agent_label} follow-up failed — using initial review.")
        return review


# ── Review Part 1: Codex primary review ──────────────────────────────────────

def _codex_primary_review(
    codex: CodexAgent,
    diff: str,
    task: str,
    plan: str,
    iteration: int,
    max_iterations: int,
    diff_summary: str = "",
    skills_context: str = "",
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
    diff_summary = diff_summary or _summarize_diff(diff)
    skills_block = f"<skills>\n{skills_context}\n</skills>\n\n" if skills_context else ""
    prompt = (
        "=== IMPORTANT: TEXT-ONLY TASK — DO NOT RUN ANY SHELL COMMANDS ===\n"
        "ALL information you need is in this prompt.\n"
        "DO NOT use any tools. DO NOT run git, cat, ls, or any other command.\n"
        "Respond with plain text only.\n"
        "=== END IMPORTANT ===\n\n"
        "<role>You are a senior technical lead doing a code review.</role>\n\n"
        f"{skills_block}"
        "<rules>\n"
        "- Flag ONLY: actual bugs, logic errors, deviations from the plan.\n"
        "- Do NOT flag: missing tests, documentation, comments, or style.\n"
        "- If the implementation correctly follows the plan, reply APPROVED.\n"
        f"- This is review cycle {iteration} of {max_iterations}. Be decisive.\n"
        "</rules>\n\n"
        f"<task>{task}</task>\n\n"
        f"<plan>{plan[:2000]}</plan>\n\n"
        f"<diff_summary>\n{diff_summary}\n</diff_summary>\n\n"
        "<output_format>\n"
        "If you need to see the full diff for specific files to make a judgment,\n"
        "respond with NEED_FULL_DIFF: filename1, filename2\n"
        "Those files' full diffs will be provided in a follow-up.\n\n"
        "Otherwise, your response MUST start with exactly one of these two lines:\n"
        "APPROVED\n"
        "CHANGES_REQUESTED\n\n"
        "If CHANGES_REQUESTED, list each confirmed issue as a numbered bullet.\n"
        "Reference specific lines/functions. No style or test requests.\n"
        "</output_format>"
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

    # Handle NEED_FULL_DIFF requests
    review = _handle_full_diff_request(
        review, diff, codex.review_query, "Codex (Review Part 1 — follow-up)",
        text_only_preamble=True,
    )

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
    diff_summary: str = "",
) -> tuple[str, bool]:
    """Have Claude validate and aggregate Codex's findings.

    Returns (aggregated_feedback, has_confirmed_issues).
    Falls back to trusting Codex directly if Claude's query fails.
    """
    diff_summary = diff_summary or _summarize_diff(diff)
    prompt = (
        "<role>You are a senior technical lead doing a secondary code review.\n"
        "Codex (another lead) reviewed the diff and flagged issues.\n"
        "Your job: validate each finding against the actual diff.</role>\n\n"
        "<rules>\n"
        "- VALID: actual bugs, logic errors, plan deviations visible in the diff.\n"
        "- INVALID: requests for tests, docs, comments, style, or anything not\n"
        "  visible in the diff below.\n"
        "- Reject hallucinated issues (issues Codex claims exist but the diff\n"
        "  shows are already handled or don't exist at all).\n"
        "</rules>\n\n"
        f"<task>{task}</task>\n\n"
        f"<plan>{plan[:2000]}</plan>\n\n"
        f"<diff_summary>\n{diff_summary}\n</diff_summary>\n\n"
        f"<review_findings>\n{codex_review}\n</review_findings>\n\n"
        "<output_format>\n"
        "If you need the full diff for specific files to validate a finding,\n"
        "respond with NEED_FULL_DIFF: filename1, filename2\n\n"
        "Otherwise, your response MUST start with exactly one of:\n"
        "CONFIRMED — at least one issue is valid\n"
        "APPROVED  — all issues are invalid, implementation is correct\n\n"
        "If CONFIRMED, list ONLY the confirmed issues as numbered bullets.\n"
        "One sentence each. Actionable. No new issues beyond what Codex flagged.\n"
        "</output_format>"
    )

    log_info("Review Part 2 — Claude is validating Codex's findings ...")
    try:
        result = claude.query(prompt)
        log_agent("Claude (Review Part 2)", result)
    except RuntimeError as exc:
        log_error(f"Claude secondary review failed: {exc} — using Codex review as-is.")
        return codex_review, True  # fall back to trusting Codex's findings

    # Handle NEED_FULL_DIFF requests
    result = _handle_full_diff_request(
        result, diff, claude.query, "Claude (Review Part 2 — follow-up)",
        text_only_preamble=False,
    )

    first_line = next((l for l in result.splitlines() if l.strip()), "")
    has_issues = bool(_CONFIRMED_PAT.search(first_line)) and not bool(_APPROVED_PAT.search(first_line))
    return result, has_issues


# ── Main review loop ─────────────────────────────────────────────────────────

def run_review(
    task: str,
    plan: str,
    claude: ClaudeAgent,
    codex: CodexAgent,
    working_dir: str,
    max_iterations: int,
    skills_context: str = "",
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
        files_changed = added = removed = 0
        for line in diff.splitlines():
            if line.startswith("diff --git"):
                files_changed += 1
            elif line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        console.print(
            f"  [bold green]Diff collected:[/] [dim]{files_changed} file(s) · "
            f"+{added} / -{removed} lines[/]"
        )

        # ── Pre-compute diff summary (shared by both review parts) ──────
        diff_summary = _summarize_diff(diff)

        # ── Review Part 1: Codex primary review ──────────────────────────
        log_phase(
            f"Review Part 1 — Codex primary review "
            f"(cycle {iteration}/{max_iterations})"
        )
        codex_review, approved = _codex_primary_review(
            codex, diff, task, plan, iteration, max_iterations,
            diff_summary=diff_summary,
            skills_context=skills_context,
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
            diff_summary=diff_summary,
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
            "<role>You are the developer. Two senior technical leads reviewed your "
            "implementation.</role>\n\n"
            "<context>\n"
            "Codex flagged issues and Claude confirmed each one is a real bug "
            "or logic error — not style, tests, or documentation.\n"
            "</context>\n\n"
            f"<review_findings>\n{aggregated}\n</review_findings>\n\n"
            f"<task>{task}</task>\n\n"
            f"<plan>{plan}</plan>\n\n"
            "<rules>\n"
            "- Fix ONLY the confirmed issues listed above.\n"
            "- Do not touch anything else. No commentary, just implement the fixes.\n"
            "</rules>"
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
