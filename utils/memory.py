"""Structured shared memory system for cross-task learning.

Maintains two complementary stores:

1. **YAML machine index** (``.orchestrator/memory.yaml``) — queryable, auto-injected
   into every agent prompt via ``get_context_for_task()``.

2. **Markdown notes** (``.orchestrator/``) — human-readable, structured entries
   per the project-memory skill format:
       bugs.md       — bug log with root cause, solution, prevention
       decisions.md  — Architectural Decision Records (ADRs)
       key_facts.md  — project config, credentials, ports, URLs
       issues.md     — work log with outcomes

Both stores are written together.  Reads pull from both for richer context.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml

from utils.logger import log_info


# ── YAML store ───────────────────────────────────────────────────────────────

_MEMORY_FILE = ".orchestrator/memory.yaml"
_MAX_ENTRIES_PER_CATEGORY = 20  # prune oldest beyond this


def _memory_path(working_dir: str) -> Path:
    return Path(working_dir) / _MEMORY_FILE


def _default_memory() -> dict:
    return {
        "patterns_learned": [],
        "codebase_conventions": [],
        "past_mistakes": [],
        "architecture_decisions": [],
        "task_index": [],
    }


def load_memory(working_dir: str) -> dict:
    """Load the shared memory file, or return defaults if it doesn't exist."""
    path = _memory_path(working_dir)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            # Ensure all categories exist
            defaults = _default_memory()
            for key in defaults:
                if key not in data:
                    data[key] = defaults[key]
            return data
        except Exception:
            return _default_memory()
    return _default_memory()


def save_memory(working_dir: str, memory: dict) -> None:
    """Save the shared memory file, pruning old entries."""
    path = _memory_path(working_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Prune each list category to max entries
    for key, val in memory.items():
        if isinstance(val, list) and len(val) > _MAX_ENTRIES_PER_CATEGORY:
            memory[key] = val[-_MAX_ENTRIES_PER_CATEGORY:]

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(memory, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def add_task_to_index(working_dir: str, task: str, outcome: str, keywords: list[str]) -> None:
    """Add a completed task to the searchable YAML index."""
    memory = load_memory(working_dir)
    memory["task_index"].append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "task": task[:200],
        "outcome": outcome,
        "keywords": keywords,
    })
    save_memory(working_dir, memory)


def add_learning(working_dir: str, category: str, entry: str) -> None:
    """Add a single learning entry to a YAML category."""
    memory = load_memory(working_dir)
    if category in memory and isinstance(memory[category], list):
        memory[category].append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "text": entry,
        })
        save_memory(working_dir, memory)


def search_past_tasks(
    working_dir: str, query: str, _memory: dict | None = None
) -> list[dict]:
    """Search the YAML task index for related past tasks.

    Pass *_memory* to reuse an already-loaded memory dict and avoid a
    redundant disk read (used by get_context_for_task).
    """
    memory = _memory if _memory is not None else load_memory(working_dir)
    query_lower = query.lower()
    words = set(re.findall(r'\w+', query_lower))

    results = []
    for entry in memory.get("task_index", []):
        task_lower = entry.get("task", "").lower()
        kw_set = set(k.lower() for k in entry.get("keywords", []))
        # Score by keyword overlap + substring match
        score = len(words & kw_set)
        if any(w in task_lower for w in words if len(w) > 3):
            score += 1
        if score > 0:
            results.append({**entry, "_score": score})

    results.sort(key=lambda x: x["_score"], reverse=True)
    return results[:5]


# ── Markdown notes store (project-memory skill) ───────────────────────────────

_PROJECT_NOTES_DIR = ".orchestrator"

_NOTE_INITIAL_CONTENT = {
    "bugs.md": (
        "# Bug Log\n\n"
        "Automatically maintained by AI Orchestrator and the project-memory skill.\n"
        "Tracks bugs with root causes, solutions, and prevention notes.\n"
    ),
    "decisions.md": (
        "# Architectural Decisions\n\n"
        "Automatically maintained by AI Orchestrator and the project-memory skill.\n"
        "Tracks key design decisions using Architectural Decision Records (ADRs).\n"
    ),
    "key_facts.md": (
        "# Key Facts\n\n"
        "Automatically maintained by AI Orchestrator and the project-memory skill.\n"
        "Tracks project configuration, conventions, important URLs, and constants.\n"
    ),
    "issues.md": (
        "# Issues / Work Log\n\n"
        "Automatically maintained by AI Orchestrator and the project-memory skill.\n"
        "Tracks completed tasks, their outcomes, and key notes.\n"
    ),
}


def _notes_path(working_dir: str, filename: str) -> Path:
    """Return the Path to a .orchestrator/ file."""
    return Path(working_dir) / _PROJECT_NOTES_DIR / filename


def _next_adr_number(working_dir: str) -> int:
    """Scan decisions.md and return the next available ADR number."""
    path = _notes_path(working_dir, "decisions.md")
    if not path.exists():
        return 1
    try:
        content = path.read_text(encoding="utf-8")
        nums = [int(n) for n in re.findall(r"ADR-(\d+)", content)]
        return max(nums) + 1 if nums else 1
    except Exception:
        return 1


def init_project_notes(working_dir: str) -> None:
    """Create ``.orchestrator/`` with the four memory files if absent.

    Idempotent — safe to call on every startup.  Existing files are never
    overwritten; only missing ones are created.
    """
    notes_dir = Path(working_dir) / _PROJECT_NOTES_DIR
    notes_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in _NOTE_INITIAL_CONTENT.items():
        path = notes_dir / filename
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    log_info(f"Project notes ready at {notes_dir}")


def _append_to_note(working_dir: str, filename: str, entry: str) -> None:
    """Append *entry* to a notes file, initialising it first if needed."""
    path = _notes_path(working_dir, filename)
    if not path.exists():
        init_project_notes(working_dir)
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)


def log_bug(
    working_dir: str,
    issue: str,
    root_cause: str = "",
    solution: str = "",
    prevention: str = "",
) -> None:
    """Append a structured bug entry to ``.orchestrator/bugs.md``.

    Format::

        ### YYYY-MM-DD - <issue title>
        - **Issue**: ...
        - **Root Cause**: ...   (omitted if empty)
        - **Solution**: ...     (omitted if empty)
        - **Prevention**: ...   (omitted if empty)
    """
    date = datetime.now().strftime("%Y-%m-%d")
    title = issue[:70].replace("\n", " ").strip()
    lines = [f"\n### {date} - {title}", f"- **Issue**: {issue.strip()}"]
    if root_cause:
        lines.append(f"- **Root Cause**: {root_cause.strip()}")
    if solution:
        lines.append(f"- **Solution**: {solution.strip()}")
    if prevention:
        lines.append(f"- **Prevention**: {prevention.strip()}")
    _append_to_note(working_dir, "bugs.md", "\n".join(lines) + "\n")


def log_decision(
    working_dir: str,
    title: str,
    context: str,
    decision: str,
    alternatives: str = "",
    consequences: str = "",
    date: str | None = None,
) -> None:
    """Append an ADR to ``.orchestrator/decisions.md`` with auto-numbering.

    Format::

        ### ADR-NNN: <title> (YYYY-MM-DD)

        **Context:**
        ...

        **Decision:**
        ...

        **Alternatives Considered:**   (omitted if empty)
        ...

        **Consequences:**              (omitted if empty)
        ...
    """
    date = date or datetime.now().strftime("%Y-%m-%d")
    n = _next_adr_number(working_dir)
    lines = [
        f"\n### ADR-{n:03d}: {title} ({date})\n",
        f"**Context:**\n{context.strip()}\n",
        f"**Decision:**\n{decision.strip()}",
    ]
    if alternatives:
        lines.append(f"\n**Alternatives Considered:**\n{alternatives.strip()}")
    if consequences:
        lines.append(f"\n**Consequences:**\n{consequences.strip()}")
    _append_to_note(working_dir, "decisions.md", "\n".join(lines) + "\n")


def log_issue(
    working_dir: str,
    task: str,
    outcome: str,
    ticket_id: str = "",
    url: str = "",
    notes: str = "",
) -> None:
    """Append a work log entry to ``.orchestrator/issues.md``.

    Format::

        ### YYYY-MM-DD - [TICKET-ID: ]<task summary>
        - **Status**: <outcome>
        - **URL**: ...    (omitted if empty)
        - **Notes**: ...  (omitted if empty)
    """
    date = datetime.now().strftime("%Y-%m-%d")
    prefix = f"{ticket_id}: " if ticket_id else ""
    summary = (prefix + task[:80]).replace("\n", " ").strip()
    lines = [
        f"\n### {date} - {summary}",
        f"- **Status**: {outcome}",
    ]
    if url:
        lines.append(f"- **URL**: {url}")
    if notes:
        lines.append(f"- **Notes**: {notes[:300].replace(chr(10), ' ').strip()}")
    _append_to_note(working_dir, "issues.md", "\n".join(lines) + "\n")


def log_key_fact(working_dir: str, category: str, fact: str, date: str | None = None) -> None:
    """Append a key fact bullet under *category* in ``.orchestrator/key_facts.md``.

    Always appends a new ``### <category>`` block.  Human review can
    consolidate duplicate sections over time.
    """
    date = date or datetime.now().strftime("%Y-%m-%d")
    entry = f"\n### {category}\n- [{date}] {fact.strip()}\n"
    _append_to_note(working_dir, "key_facts.md", entry)


# ── Markdown context reader ───────────────────────────────────────────────────

def _read_markdown_context(working_dir: str, task: str) -> str:
    """Search ``.orchestrator/`` and return relevant excerpts for *task*.

    Splits each file into ``###`` sections and scores them by keyword overlap
    with the task.  Returns a formatted string of the top matches, or ``""``
    if the files don't exist or nothing is relevant.
    """
    keywords = set(w for w in extract_keywords_from_task(task) if len(w) > 3)
    if not keywords:
        return ""

    def _score_section(text: str) -> int:
        tl = text.lower()
        return sum(1 for kw in keywords if kw in tl)

    def _top_sections(filename: str, max_results: int = 3, max_chars: int = 250) -> list[str]:
        path = _notes_path(working_dir, filename)
        if not path.exists():
            return []
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return []
        # Split on ### headings (keep the heading with the block)
        blocks = re.split(r"(?=\n### )", content)
        scored = [(b, _score_section(b)) for b in blocks if b.strip()]
        scored = [(b, s) for b, s in scored if s > 0]
        scored.sort(key=lambda x: x[1], reverse=True)
        results = []
        for block, _ in scored[:max_results]:
            first_line = block.strip().splitlines()[0].lstrip("# ").strip()
            excerpt = block.strip()[:max_chars].replace("\n", " ")
            results.append(f"  - {first_line}: {excerpt}")
        return results

    parts = []
    bug_hits = _top_sections("bugs.md")
    if bug_hits:
        parts.append("RELEVANT BUGS (.orchestrator/bugs.md):\n" + "\n".join(bug_hits))
    dec_hits = _top_sections("decisions.md")
    if dec_hits:
        parts.append("RELEVANT DECISIONS (.orchestrator/decisions.md):\n" + "\n".join(dec_hits))
    fact_hits = _top_sections("key_facts.md")
    if fact_hits:
        parts.append("KEY FACTS (.orchestrator/key_facts.md):\n" + "\n".join(fact_hits))

    return "\n\n".join(parts)


# ── Combined context builder ──────────────────────────────────────────────────

def get_context_for_task(working_dir: str, task: str) -> str:
    """Build a context string from both stores relevant to the current task.

    Combines:
    - YAML index: architecture decisions, conventions, past mistakes, related tasks
    - Markdown notes: relevant bugs, decisions, and key facts from .orchestrator/

    Returns a formatted string to prepend to agent prompts.
    """
    memory = load_memory(working_dir)
    sections = []

    # Recent architecture decisions (YAML)
    arch = memory.get("architecture_decisions", [])
    if arch:
        items = arch[-5:]
        lines = [f"  - {e['text']}" for e in items if isinstance(e, dict)]
        if lines:
            sections.append("ARCHITECTURE DECISIONS:\n" + "\n".join(lines))

    # Codebase conventions (YAML)
    conv = memory.get("codebase_conventions", [])
    if conv:
        items = conv[-5:]
        lines = [f"  - {e['text']}" for e in items if isinstance(e, dict)]
        if lines:
            sections.append("CODEBASE CONVENTIONS:\n" + "\n".join(lines))

    # Past mistakes (YAML)
    mistakes = memory.get("past_mistakes", [])
    if mistakes:
        items = mistakes[-3:]
        lines = [f"  - {e['text']}" for e in items if isinstance(e, dict)]
        if lines:
            sections.append("PAST MISTAKES TO AVOID:\n" + "\n".join(lines))

    # Related past tasks (YAML keyword search — reuse already-loaded memory)
    related = search_past_tasks(working_dir, task, _memory=memory)
    if related:
        lines = [f"  - [{e['date']}] {e['task']} ({e['outcome']})" for e in related]
        sections.append("RELATED PAST TASKS:\n" + "\n".join(lines))

    # Markdown notes (keyword-matched excerpts from .orchestrator/)
    md_context = _read_markdown_context(working_dir, task)
    if md_context:
        sections.append(md_context)

    if not sections:
        return ""

    return "SHARED MEMORY (from previous tasks):\n" + "\n\n".join(sections) + "\n\n"


def extract_keywords_from_task(task: str) -> list[str]:
    """Extract meaningful keywords from a task description."""
    # Remove common stop words and short words
    stop = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
            "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
            "it", "this", "that", "from", "by", "as", "do", "not", "can",
            "will", "should", "would", "could", "have", "has", "had", "all",
            "each", "every", "some", "any", "my", "your", "our", "their",
            "its", "just", "also", "very", "too", "only", "own", "same",
            "than", "then", "now", "here", "there", "when", "where", "how",
            "what", "which", "who", "whom", "why", "make", "add", "use",
            "get", "set", "put", "new", "old", "want", "need", "like"}
    words = re.findall(r'\b[a-zA-Z_]\w{2,}\b', task.lower())
    return list(dict.fromkeys(w for w in words if w not in stop))[:15]
