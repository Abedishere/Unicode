"""Structured shared memory system for cross-task learning.

Maintains a YAML-based memory file (.orchestrator/memory.yaml) with categorized
knowledge that persists across tasks. Each task can read relevant past context
and write new learnings.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml

from utils.logger import log_info


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
    """Add a completed task to the searchable index."""
    memory = load_memory(working_dir)
    memory["task_index"].append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "task": task[:200],
        "outcome": outcome,
        "keywords": keywords,
    })
    save_memory(working_dir, memory)


def add_learning(working_dir: str, category: str, entry: str) -> None:
    """Add a single learning entry to a category."""
    memory = load_memory(working_dir)
    if category in memory and isinstance(memory[category], list):
        memory[category].append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "text": entry,
        })
        save_memory(working_dir, memory)


def search_past_tasks(working_dir: str, query: str) -> list[dict]:
    """Search the task index for related past tasks."""
    memory = load_memory(working_dir)
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


def get_context_for_task(working_dir: str, task: str) -> str:
    """Build a context string from shared memory relevant to the current task.

    Returns a formatted string to prepend to agent prompts.
    """
    memory = load_memory(working_dir)
    sections = []

    # Recent architecture decisions
    arch = memory.get("architecture_decisions", [])
    if arch:
        items = arch[-5:]
        lines = [f"  - {e['text']}" for e in items if isinstance(e, dict)]
        if lines:
            sections.append("ARCHITECTURE DECISIONS:\n" + "\n".join(lines))

    # Codebase conventions
    conv = memory.get("codebase_conventions", [])
    if conv:
        items = conv[-5:]
        lines = [f"  - {e['text']}" for e in items if isinstance(e, dict)]
        if lines:
            sections.append("CODEBASE CONVENTIONS:\n" + "\n".join(lines))

    # Past mistakes (so we don't repeat them)
    mistakes = memory.get("past_mistakes", [])
    if mistakes:
        items = mistakes[-3:]
        lines = [f"  - {e['text']}" for e in items if isinstance(e, dict)]
        if lines:
            sections.append("PAST MISTAKES TO AVOID:\n" + "\n".join(lines))

    # Related past tasks
    related = search_past_tasks(working_dir, task)
    if related:
        lines = [f"  - [{e['date']}] {e['task']} ({e['outcome']})" for e in related]
        sections.append("RELATED PAST TASKS:\n" + "\n".join(lines))

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
