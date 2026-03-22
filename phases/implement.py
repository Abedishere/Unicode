from __future__ import annotations

import concurrent.futures
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.table import Table

from agents.claude_agent import ClaudeAgent
from utils.logger import log_info, log_phase, log_success
from utils.memory import get_context_for_task, load_memory, log_bug, log_key_fact, parse_json_response, save_memory

try:
    from utils.repo_map import generate_repo_map as _generate_repo_map
except ImportError:
    def _generate_repo_map(working_dir: str, max_tokens: int = 2000) -> str:  # type: ignore[misc]
        return ""

console = Console()

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


def _build_file_prompt(
    task: str,
    file_spec,
    skeleton: str,
    shared_deps: str,
    memory_context: str,
) -> str:
    action_hint = (
        "Create this file from scratch."
        if file_spec.action == "CREATE"
        else "Modify this existing file."
    )
    return (
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


def _implement_file_by_file(
    task: str,
    structured_plan: StructuredPlan,
    claude: ClaudeAgent,
    repo_map: str = "",
    memory_context: str = "",
    qwen: QwenAgent | None = None,
    work_dir: str = "",
    max_workers: int = 5,
) -> str:
    """Implement the plan with up to *max_workers* parallel Claude agents.

    Uses a work-queue pattern so each worker regenerates the repo_map just
    before building its prompt.  Files completed by earlier workers are
    already on disk, so later workers see their real function signatures —
    no separate contracts pass needed.

    A Rich Live table shows per-file status.  Qwen memory synthesis runs
    sequentially after all files complete.
    """
    total = len(structured_plan.files)
    repo_map_tokens = 2000  # same default as orchestrator

    shared_deps = (
        "<shared_dependencies>\n"
        f"{structured_plan.shared_dependencies}\n"
        "</shared_dependencies>\n\n"
        if structured_plan.shared_dependencies else ""
    )

    # Suppress individual Live spinners — the progress table handles display.
    claude._quiet = True
    if qwen is not None:
        qwen._quiet = True

    _colors = {
        "Pending": "dim", "Running": "yellow",
        "Done": "green", "Error": "red",
    }
    status: dict[str, str] = {s.path: "Pending" for s in structured_plan.files}
    outputs: dict[str, str] = {}
    _lock = threading.Lock()
    update_event = threading.Event()
    stop_event = threading.Event()

    # Work queue: workers pop the next file atomically, regenerating the
    # repo_map at that moment so completed files are already on disk.
    _queue = list(structured_plan.files)

    def _dequeue_with_skeleton() -> tuple | None:
        """Pop the next file and snapshot the current repo_map under the lock."""
        with _lock:
            if not _queue:
                return None
            spec = _queue.pop(0)
            current_map = (
                _generate_repo_map(work_dir, repo_map_tokens)
                if work_dir else repo_map
            )
        skeleton = f"<codebase>\n{current_map}\n</codebase>\n\n" if current_map else ""
        return spec, skeleton

    def _make_table() -> Table:
        t = Table(show_header=True, header_style="bold", box=None, expand=True)
        t.add_column("File", no_wrap=False)
        t.add_column("Status", no_wrap=True, width=9)
        done_n = sum(1 for s in status.values() if s == "Done")
        err_n  = sum(1 for s in status.values() if s == "Error")
        run_n  = sum(1 for s in status.values() if s == "Running")
        t.add_row(
            f"[dim]Progress: {done_n + err_n}/{total}  "
            f"({run_n} running, {err_n} errors)[/]",
            "",
        )
        for path, state in status.items():
            c = _colors.get(state, "white")
            t.add_row(f"  {path}", f"[{c}]{state}[/]")
        return t

    def _refresh(live: Live) -> None:
        while not stop_event.is_set():
            update_event.wait(timeout=120)
            update_event.clear()
            with _lock:
                t = _make_table()
            live.update(t)
            live.refresh()

    def _worker() -> None:
        """Each thread loops: dequeue → implement → repeat until queue empty."""
        while True:
            item = _dequeue_with_skeleton()
            if item is None:
                return
            file_spec, skeleton = item

            with _lock:
                status[file_spec.path] = "Running"
            update_event.set()

            prompt = _build_file_prompt(task, file_spec, skeleton, shared_deps, memory_context)
            try:
                out = claude.implement(prompt)
                with _lock:
                    status[file_spec.path] = "Done"
                    outputs[file_spec.path] = out
            except Exception:
                with _lock:
                    status[file_spec.path] = "Error"
                    outputs[file_spec.path] = ""
            update_event.set()

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        # Launch exactly max_workers threads; each drains the queue itself.
        worker_futures = [pool.submit(_worker) for _ in range(min(max_workers, total))]
        with Live(_make_table(), auto_refresh=False, console=console) as live:
            refresh_thread = threading.Thread(target=_refresh, args=(live,), daemon=True)
            refresh_thread.start()
            try:
                concurrent.futures.wait(worker_futures)
            finally:
                stop_event.set()
                update_event.set()
                refresh_thread.join(timeout=1.0)
            with _lock:
                live.update(_make_table())
            live.refresh()
    finally:
        pool.shutdown(wait=False)
        claude._quiet = False
        if qwen is not None:
            qwen._quiet = False

    # Log results
    all_lessons: list[str] = []
    results = []
    for spec in structured_plan.files:
        state = status[spec.path]
        out = outputs.get(spec.path, "")
        if state == "Done":
            log_success(f"  {spec.path} — done")
            results.append(f"[{spec.path}] done")
        else:
            log_info(f"  {spec.path} — {state.lower()}")
            results.append(f"[{spec.path}] {state.lower()}")

        if qwen and work_dir and state == "Done":
            lessons = _synthesize_file_memory(
                qwen, task, spec.path, spec.spec, out, work_dir,
            )
            all_lessons.extend(lessons)

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
    max_workers: int = 5,
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
            qwen=qwen, work_dir=work_dir, max_workers=max_workers,
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
