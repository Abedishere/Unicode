from __future__ import annotations

import concurrent.futures
import json
import re
import threading
import time
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


def _extract_contracts(
    qwen: QwenAgent,
    task: str,
    structured_plan: StructuredPlan,
) -> str:
    """Use Qwen to extract expected public interfaces from all CREATE files.

    Runs once before parallel workers start. Returns a compact contracts string
    listing function/class signatures for each file being created. Injected into
    every worker prompt so agents know what sibling files will expose — preventing
    callers from referencing functions that don't exist yet.
    """
    create_files = [f for f in structured_plan.files if f.action == "CREATE"]
    if len(create_files) < 2:
        return ""  # no sibling race if only one file is being created

    specs_block = "\n\n".join(
        f"FILE: {f.path}\n{f.spec[:600]}"
        for f in create_files
    )

    prompt = (
        f"<task>{task[:200]}</task>\n\n"
        "These files will be created in parallel as part of this task. "
        "For each file, extract its expected public interface based solely on its spec.\n\n"
        f"<file_specs>\n{specs_block}\n</file_specs>\n\n"
        "Output format (repeat for each file):\n"
        "FILE: path/to/file.py\n"
        "- def function_name(param: type) -> return_type\n"
        "- class ClassName\n\n"
        "Rules:\n"
        "- Public API only (functions/classes other files will import)\n"
        "- Use the exact names from the spec\n"
        "- If a spec has no clear public API, skip that file\n"
        "- No prose, no explanations, no implementation details\n"
        "Return ONLY the FILE blocks."
    )

    try:
        result = qwen.query(prompt)
        if result.strip():
            return (
                "<sibling_contracts>\n"
                "These files are being created in parallel — match their interfaces exactly:\n"
                f"{result.strip()}\n"
                "</sibling_contracts>\n\n"
            )
    except Exception:
        pass
    return ""


def _build_file_prompt(
    task: str,
    file_spec,
    skeleton: str,
    shared_deps: str,
    memory_context: str,
    contracts: str = "",
    skills_context: str = "",
) -> str:
    action_hint = (
        "Create this file from scratch."
        if file_spec.action == "CREATE"
        else "Modify this existing file."
    )
    skills_block = f"<skills>\n{skills_context}\n</skills>\n\n" if skills_context else ""
    return (
        f"{memory_context}"
        f"<task>{task[:500]}</task>\n\n"
        f"{skeleton}"
        f"{contracts}"
        f"{skills_block}"
        f"{shared_deps}"
        f"<file_spec>\n"
        f"ACTION: {file_spec.action} {file_spec.path}\n"
        f"{file_spec.spec}\n"
        f"</file_spec>\n\n"
        "<rules>\n"
        f"- Implement ONLY this file: {file_spec.path}\n"
        f"- {action_hint}\n"
        "- Use the shared dependency names exactly as listed above.\n"
        "- When importing from sibling files listed in <sibling_contracts>, "
        "use the exact function/class names shown there.\n"
        "- Follow the spec precisely. No extras.\n"
        "- When creating or modifying requirements.txt or pyproject.toml, "
        "always pin package versions with a minimum version constraint "
        "(e.g. `click>=8.1.0`, not just `click`).\n"
        "</rules>"
    )


def _snapshot_dir(work_dir: str) -> set[str]:
    """Return all relative file paths under work_dir, excluding .orchestrator."""
    root = Path(work_dir)
    try:
        return {
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file() and ".orchestrator" not in p.parts
        }
    except Exception:
        return set()


def _wait_for_file(path: Path, retries: int = 5, delay: float = 0.4) -> bool:
    """Poll for file existence to handle OS-level write-flush delays on Windows."""
    for _ in range(retries):
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(delay)  # only reached when file not yet visible
    return False


def _check_file_written(
    spec_path: str,
    expected: Path,
    new_files: set[str],
    output: str,
    work_dir: str,
) -> bool:
    """Check whether a file landed on disk via any of three recovery layers.

    Layer 1: Claude used the Write tool — poll with OS-flush tolerance (Windows).
    Layer 2: Claude wrote a different path due to session conflict/rate limit.
    Layer 3: Claude emitted content as text instead of using the Write tool.
    """
    # Layer 1: exact path with OS-flush polling
    file_found = _wait_for_file(expected)

    # Layer 2: Claude wrote a differently-named file — accept it
    if not file_found and new_files:
        file_found = True
        log_info(f"  {spec_path} — written as: {sorted(new_files)}")

    # Layer 3: Claude output the content as text — write it ourselves
    if not file_found:
        file_found = _extract_and_write_file(spec_path, output, work_dir)
        if file_found:
            log_info(f"  {spec_path} — recovered from Claude output text")

    return file_found


def _extract_and_write_file(file_path: str, output: str, work_dir: str) -> bool:
    """Write a file from Claude's text output when the tool write didn't fire.

    Claude sometimes outputs file content as a markdown code block instead of
    using the Write tool (e.g. under session conflicts or rate limits).  This
    function extracts the largest code block and writes it to the expected path.
    """
    if not output or not output.strip():
        return False

    target = Path(work_dir) / file_path if work_dir else Path(file_path)

    # Extract all fenced code blocks and pick the largest one
    blocks = re.findall(r"```(?:[a-zA-Z0-9_.+-]*)?\n([\s\S]*?)```", output)
    if blocks:
        content = max(blocks, key=len).strip()
        if len(content) > 10:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return target.exists() and target.stat().st_size > 0

    # Last resort: use the raw output if it looks like code (not prose)
    stripped = output.strip()
    first_word = stripped.split()[0] if stripped.split() else ""
    looks_like_prose = first_word in {"I", "The", "Here", "This", "Note", "Sure", "Let", "To"}
    if stripped and not looks_like_prose and len(stripped.splitlines()) >= 3:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(stripped, encoding="utf-8")
        return target.exists() and target.stat().st_size > 0

    return False


def _implement_file_by_file(
    task: str,
    structured_plan: StructuredPlan,
    claude: ClaudeAgent,
    repo_map: str = "",
    memory_context: str = "",
    qwen: QwenAgent | None = None,
    work_dir: str = "",
    max_workers: int = 5,
    skills_context: str = "",
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

    # Extract cross-file interface contracts so parallel workers know what
    # sibling files will expose (prevents referencing non-existent functions).
    contracts = ""
    if qwen is not None and total > 1:
        log_info("Extracting cross-file contracts via Qwen ...")
        contracts = _extract_contracts(qwen, task, structured_plan)
        if contracts:
            log_info("Contracts extracted — injecting into worker prompts.")

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

            prompt = _build_file_prompt(task, file_spec, skeleton, shared_deps, memory_context, contracts, skills_context)
            try:
                # Snapshot directory before so we can detect files written under
                # a different name/path than the plan specifies.
                before = _snapshot_dir(work_dir) if work_dir else set()

                out = claude.implement(prompt)

                after = _snapshot_dir(work_dir) if work_dir else set()
                new_files = after - before

                expected = Path(work_dir) / file_spec.path if work_dir else Path(file_spec.path)
                file_found = _check_file_written(file_spec.path, expected, new_files, out, work_dir)

                with _lock:
                    if file_found:
                        status[file_spec.path] = "Done"
                    else:
                        status[file_spec.path] = "Missing"
                        log_info(f"  Warning: {file_spec.path} not found on disk after implementation")
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

    # Sequential retry for files that didn't land on disk despite a clean exit.
    # Runs single-threaded to avoid the same session-conflict race that caused
    # the missing files in the first place.
    missing = [s for s in structured_plan.files if status[s.path] in ("Missing", "Error")]
    if missing:
        log_info(f"  Retrying {len(missing)} missing/failed file(s) sequentially ...")
        for spec in missing:
            current_map = _generate_repo_map(work_dir, repo_map_tokens) if work_dir else ""
            skeleton = f"<codebase>\n{current_map}\n</codebase>\n\n" if current_map else ""
            prompt = _build_file_prompt(task, spec, skeleton, shared_deps, memory_context, contracts, skills_context)
            try:
                before = _snapshot_dir(work_dir) if work_dir else set()
                out = claude.implement(prompt)
                after = _snapshot_dir(work_dir) if work_dir else set()
                new_files = after - before

                expected = Path(work_dir) / spec.path if work_dir else Path(spec.path)
                file_found = _check_file_written(spec.path, expected, new_files, out, work_dir)

                if file_found:
                    status[spec.path] = "Done"
                    outputs[spec.path] = out
                    log_success(f"  {spec.path} — recovered")
                else:
                    log_info(f"  {spec.path} — still missing after retry")
            except Exception as exc:
                log_info(f"  {spec.path} — retry failed: {exc}")

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
    skills_context: str = "",
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
            skills_context=skills_context,
        )

    # Monolithic fallback
    log_info("Using monolithic implementation (unstructured plan)")
    context_brief = _build_context_brief(discussion)

    skeleton = ""
    if repo_map:
        skeleton = f"<codebase>\n{repo_map}\n</codebase>\n\n"

    log_info(f"Running Claude Code (dev:{claude.dev_model}) ...")
    skills_block = f"<skills>\n{skills_context}\n</skills>\n\n" if skills_context else ""
    implement_prompt = (
        f"{memory_context}"
        f"{skeleton}"
        f"{context_brief}"
        f"{skills_block}"
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
