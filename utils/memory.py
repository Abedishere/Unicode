"""Structured shared memory system for cross-task learning.

Maintains a searchable YAML task index (``.orchestrator/memory.yaml``) and
delegates pattern storage to two dedicated modules:

- ``utils/local_memory.py``  — project-specific patterns (.orchestrator/local_patterns.yaml)
- ``utils/global_memory.py`` — cross-project patterns (~/.unicode/global/)

Both are queried by ``get_context_for_task()`` and injected into every agent prompt.
"""

from __future__ import annotations

import functools
import json
import re
from datetime import datetime
from pathlib import Path

import yaml

from utils.logger import log_info


def parse_json_response(raw: str) -> dict:
    """Parse a JSON object from an LLM response, stripping markdown fences."""
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\n?```$", "", raw.strip())
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        return json.loads(match.group()) if match else {}
    except (json.JSONDecodeError, AttributeError):
        return {}


# ── YAML store ───────────────────────────────────────────────────────────────

_MEMORY_FILE = ".orchestrator/memory.yaml"
_MAX_ENTRIES_PER_CATEGORY = 20


def _memory_path(working_dir: str) -> Path:
    return Path(working_dir) / _MEMORY_FILE


def _task_emb_path(working_dir: str) -> Path:
    return Path(working_dir) / ".orchestrator" / "task_index_emb.npy"


def _default_memory() -> dict:
    return {"task_index": []}


def load_memory(working_dir: str) -> dict:
    path = _memory_path(working_dir)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            defaults = _default_memory()
            for key in defaults:
                if key not in data:
                    data[key] = defaults[key]
            return data
        except Exception:
            return _default_memory()
    return _default_memory()


def save_memory(working_dir: str, memory: dict) -> None:
    path = _memory_path(working_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    for key, val in memory.items():
        if isinstance(val, list) and len(val) > _MAX_ENTRIES_PER_CATEGORY:
            memory[key] = val[-_MAX_ENTRIES_PER_CATEGORY:]
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(memory, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def add_task_to_index(
    working_dir: str,
    task: str,
    outcome: str,
    keywords: list[str],
    quality_score: float = 0.5,
) -> None:
    """Add a completed task to the searchable YAML index."""
    memory = load_memory(working_dir)
    memory["task_index"].append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "task": task[:200],
        "outcome": outcome,
        "keywords": keywords,
        "quality_score": round(quality_score, 3),
    })
    save_memory(working_dir, memory)

    # Append embedding for the new task entry
    try:
        from utils.embeddings import is_available, encode_document, load_emb, save_emb
        import numpy as np
        if is_available():
            all_tasks = memory.get("task_index", [])
            n_total = len(all_tasks)
            text = task[:200] + " " + " ".join(keywords)
            new_emb = encode_document([text])
            if new_emb is not None:
                ep = _task_emb_path(working_dir)
                existing_emb = load_emb(ep, n_total - 1)
                if existing_emb is not None:
                    combined = np.vstack([existing_emb, new_emb])
                else:
                    # Rebuild from all entries (handles first run or length mismatch)
                    texts = [
                        e.get("task", "")[:200] + " " + " ".join(e.get("keywords", []))
                        for e in all_tasks
                    ]
                    combined = encode_document(texts)
                if combined is not None:
                    save_emb(ep, combined)
    except Exception:
        pass


def compute_quality_score(
    approved: bool,
    review_cycles: int,
    file_statuses: dict[str, str],
    fallback_triggered: bool,
) -> float:
    """Compute a 0-1 quality score for a completed task run."""
    if not approved:
        return 0.0
    bad = sum(1 for s in file_statuses.values() if s in {"Error", "Limit", "Missing"})
    error_rate = bad / max(len(file_statuses), 1)
    score = 1.0
    score -= min(review_cycles * 0.1, 0.3)
    score -= min(error_rate * 0.4, 0.4)
    score -= 0.2 if fallback_triggered else 0.0
    return round(max(0.0, score), 3)


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokenizer for BM25 corpus and queries."""
    return re.findall(r'\b[a-z][a-z0-9_]*\b', text.lower())


def search_past_tasks(
    working_dir: str, query: str, _memory: dict | None = None
) -> list[dict]:
    """Search the YAML task index for related past tasks.

    Uses semantic cosine similarity when fastembed is available; falls back to BM25Plus.
    Pass *_memory* to reuse an already-loaded dict and avoid a redundant disk read.
    """
    memory = _memory if _memory is not None else load_memory(working_dir)
    entries = memory.get("task_index", [])
    if not entries:
        return []

    # ── Semantic + MMR path ───────────────────────────────────────────────────
    try:
        from utils.embeddings import (
            is_available, encode_query, encode_document, cosine_scores, load_emb, save_emb,
            SEARCH_THRESHOLD, mmr_rerank,
        )
        import numpy as np
        if is_available():
            q_emb = encode_query([query])
            if q_emb is not None:
                ep = _task_emb_path(working_dir)
                corpus_emb = load_emb(ep, len(entries))
                if corpus_emb is None:
                    texts = [
                        e.get("task", "")[:200] + " " + " ".join(e.get("keywords", []))
                        for e in entries
                    ]
                    corpus_emb = encode_document(texts)
                    if corpus_emb is not None:
                        save_emb(ep, corpus_emb)
                if corpus_emb is not None:
                    sims = cosine_scores(q_emb[0], corpus_emb)
                    keep = np.where(np.asarray(sims) >= SEARCH_THRESHOLD)[0]
                    if keep.size:
                        rel = np.array([
                            float(sims[i]) * (0.5 + 0.5 * entries[i].get("quality_score", 0.5))
                            for i in keep
                        ])
                        sel = mmr_rerank(q_emb[0], corpus_emb[keep], rel, n=5)
                        return [{**entries[keep[i]], "_score": float(rel[i])} for i in sel]
                    return []
    except Exception:
        pass

    # ── BM25 fallback ─────────────────────────────────────────────────────────
    try:
        from rank_bm25 import BM25Plus
        corpus = [
            _tokenize(
                e.get("task", "") + " "
                + e.get("outcome", "") + " "
                + " ".join(e.get("keywords", []))
            )
            for e in entries
        ]
        if not any(corpus):
            return []
        bm25 = BM25Plus(corpus)
        query_tokens = _tokenize(query)
        raw_scores = bm25.get_scores(query_tokens)
        results = []
        for entry, raw in zip(entries, raw_scores):
            if raw <= 0:
                continue
            q = entry.get("quality_score", 0.5)
            results.append({**entry, "_score": raw * (0.5 + 0.5 * q)})
        results.sort(key=lambda x: x["_score"], reverse=True)
        return results[:5]
    except Exception:
        return []


# ── Combined context builder ──────────────────────────────────────────────────

@functools.lru_cache(maxsize=8)
def get_context_for_task(working_dir: str, task: str) -> str:
    """Build a context string relevant to the current task.

    Sources (in order):
    1. Global cross-project patterns from ~/.unicode/global/
    2. Local project patterns from .orchestrator/local_patterns.yaml
    3. Related past tasks from .orchestrator/memory.yaml

    Cached per (working_dir, task) pair within a single run.
    """
    sections = []

    try:
        from utils.global_memory import load_global_patterns, format_global_context, increment_usage_counts
        global_pats = load_global_patterns(task, n=8)
        if global_pats:
            increment_usage_counts([p["id"] for p in global_pats])
            sections.append(format_global_context(global_pats))
    except Exception:
        pass

    try:
        from utils.local_memory import load_local_patterns, format_local_context, increment_local_usage_counts
        local_pats = load_local_patterns(working_dir, task, n=8)
        if local_pats:
            increment_local_usage_counts(working_dir, [p["id"] for p in local_pats])
            sections.append(format_local_context(local_pats))
    except Exception:
        pass

    related = search_past_tasks(working_dir, task)
    if related:
        lines = [f"  - [{e['date']}] {e['task']} ({e['outcome']})" for e in related]
        sections.append("RELATED PAST TASKS:\n" + "\n".join(lines))

    return "\n\n".join(sections) + "\n\n" if sections else ""


def extract_keywords_from_task(task: str) -> list[str]:
    """Extract meaningful keywords from a task description."""
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
