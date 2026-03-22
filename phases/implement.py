from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from agents.claude_agent import ClaudeAgent
from utils.logger import log_info, log_phase, log_success
from utils.memory import get_context_for_task, load_memory, log_bug, log_key_fact, parse_json_response, save_memory

if TYPE_CHECKING:
    from agents.qwen_agent import QwenAgent
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
        "<context>\n"
        + "\n".join(lines)
        + "\n</context>\n\n"
    )


def _synthesize_file_memory(
    qwen: QwenAgent,
    task: str,
    file_path: str,
    file_spec: str,
    claude_output: str,
    work_dir: str,
) -> list[str]:
    """Ask Qwen to extract memory entries from a single file's implementation.

    Runs after each Claude subagent completes a file. Writes any discovered
    conventions, patterns, or bugs to .orchestrator/ immediately so they are
    available to subsequent subagents via memory_context.

    Returns the list of lessons found (caller accumulates and writes them once).
    """
    prompt = (
        "A Claude subagent just implemented one file. Extract memory entries.\n\n"
        f"<task>{task[:200]}</task>\n\n"
        f"<file_spec>\n"
        f"FILE: {file_path}\n"
        f"SPEC: {file_spec[:300]}\n"
        f"</file_spec>\n\n"
        f"<context>\n"
        f"CLAUDE OUTPUT (what the agent reported doing):\n{claude_output[:800]}\n"
        f"</context>\n\n"
        "<output_format>\n"
        "Return a JSON object (omit any key if nothing real to record):\n"
        "{\n"
        '  "key_facts": [{"category": "Code Conventions", "fact": "All handlers are async"}],\n'
        '  "bugs": [{"issue": "...", "root_cause": "...", "solution": "...", "prevention": "..."}],\n'
        '  "lessons": ["short concrete lesson"]\n'
        "}\n"
        "</output_format>\n\n"
        "Only record things specific to THIS file and THIS task. No generic advice.\n"
        "Return ONLY valid JSON, no markdown fences."
    )
    try:
        raw = qwen.query(prompt)
        data = parse_json_response(raw)
    except Exception:
        return []

    for entry in data.get("key_facts", []):
        if isinstance(entry, dict) and entry.get("fact"):
            log_key_fact(work_dir, entry.get("category", "General"), entry["fact"])

    for bug in data.get("bugs", []):
        if isinstance(bug, dict) and bug.get("issue"):
            log_bug(
                working_dir=work_dir,
                issue=bug["issue"],
                root_cause=bug.get("root_cause", ""),
                solution=bug.get("solution", ""),
                prevention=bug.get("prevention", ""),
            )

    return [l for l in data.get("lessons", []) if isinstance(l, str) and l.strip()]


def _implement_file_by_file(
    task: str,
    structured_plan: StructuredPlan,
    claude: ClaudeAgent,
    repo_map: str = "",
    memory_context: str = "",
    qwen: QwenAgent | None = None,
    work_dir: str = "",
) -> str:
    """Implement the plan one file at a time.

    Each file gets a focused prompt with only the repo skeleton, shared
    dependencies, and that file's specific spec.  This produces more
    reliable output for larger projects since each call has a smaller,
    focused context.
    """
    total = len(structured_plan.files)
    results = []
    all_lessons: list[str] = []

    skeleton = ""
    if repo_map:
        skeleton = f"<codebase>\n{repo_map}\n</codebase>\n\n"

    shared_deps = ""
    if structured_plan.shared_dependencies:
        shared_deps = (
            "<shared_dependencies>\n"
            f"{structured_plan.shared_dependencies}\n"
            "</shared_dependencies>\n\n"
        )

    # Suppress Qwen's run_cli Live display during per-file memory synthesis —
    # it would conflict with log_info/log_success output between Claude calls.
    if qwen is not None:
        qwen._quiet = True

    for i, file_spec in enumerate(structured_plan.files, 1):
        log_info(f"Implementing file {i}/{total}: {file_spec.path}")

        action_hint = (
            "Create this file from scratch."
            if file_spec.action == "CREATE"
            else "Modify this existing file."
        )

        prompt = (
            f"{memory_context}"
            f"<task>{task[:500]}</task>\n\n"
            f"{skeleton}"
            f"{shared_deps}"
            f"<file_spec>\n"
            f"ACTION: {file_spec.action} {file_spec.path}\n"
            f"{file_spec.spec}\n"
            f"</file_spec>\n\n"
            "<rules>\n"
            f"- Implement ONLY this file: {file_spec.path}\n"
            f"- {action_hint}\n"
            "- Use the shared dependency names exactly as listed above.\n"
            "- Follow the spec precisely. No extras.\n"
            "- When creating or modifying requirements.txt or pyproject.toml, "
            "always pin package versions with a minimum version constraint "
            "(e.g. `click>=8.1.0`, not just `click`).\n"
            "</rules>"
        )

        claude_output = claude.implement(prompt)
        results.append(f"[{file_spec.path}] done")
        log_success(f"  {file_spec.path} — done")

        if qwen and work_dir:
            lessons = _synthesize_file_memory(
                qwen, task, file_spec.path, file_spec.spec, claude_output, work_dir,
            )
            all_lessons.extend(lessons)
            # Refresh memory_context so the next file's Claude subagent benefits
            # from conventions/bugs discovered in this file
            if i < total:
                memory_context = get_context_for_task(work_dir, task)

    if qwen is not None:
        qwen._quiet = False

    if all_lessons and work_dir:
        memory = load_memory(work_dir)
        for lesson in all_lessons:
            memory["past_mistakes"].append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "text": lesson,
            })
        save_memory(work_dir, memory)

    return f"File-by-file implementation complete ({total} files):\n" + "\n".join(results)


def run_implementation(
    task: str,
    plan: str,
    claude: ClaudeAgent,
    discussion: list[dict[str, str]] | None = None,
    memory_context: str = "",
    repo_map: str = "",
    structured_plan: StructuredPlan | None = None,
    qwen: QwenAgent | None = None,
    work_dir: str = "",
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
            qwen=qwen, work_dir=work_dir,
        )

    # Monolithic fallback
    log_info("Using monolithic implementation (unstructured plan)")
    context_brief = _build_context_brief(discussion)

    skeleton = ""
    if repo_map:
        skeleton = f"<codebase>\n{repo_map}\n</codebase>\n\n"

    log_info(f"Running Claude Code (dev:{claude.dev_model}) ...")
    implement_prompt = (
        f"{memory_context}"
        f"{skeleton}"
        f"{context_brief}"
        f"<task>\n{task}\n</task>\n\n"
        f"<plan>\n{plan}\n</plan>\n\n"
        "<rules>\n"
        "- Implement the plan exactly. Follow every step.\n"
        "- When creating or modifying requirements.txt or pyproject.toml, "
        "always pin package versions with a minimum version constraint "
        "(e.g. `click>=8.1.0`, not just `click`). "
        "Look up the current stable version of each package and use it as the lower bound.\n"
        "</rules>"
    )
    result = claude.implement(implement_prompt)
    log_success("Claude Code finished implementation.")
    return result
