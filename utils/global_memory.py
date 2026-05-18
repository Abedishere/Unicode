"""Cross-project global memory store.

Patterns learned from any project are written to ~/.unicode/global/ and
surfaced when starting work on any new project.  Only project-agnostic
insights qualify (no file paths, port numbers, or project-specific nouns).

Two files:
  global_patterns.yaml   machine-written index, BM25-searchable
  global_wisdom.md       human-readable append-only log
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

import yaml
from rank_bm25 import BM25Plus

from utils.memory import _tokenize


# ── Helpers ───────────────────────────────────────────────────────────────────

def _share_n_consecutive_words(a: str, b: str, n: int = 5) -> bool:
    """Return True if strings *a* and *b* share at least *n* consecutive words."""
    words_a = a.lower().split()
    words_b_set = set(b.lower().split())
    if len(words_a) < n:
        return False
    ngrams = [" ".join(words_a[i:i + n]) for i in range(len(words_a) - n + 1)]
    words_b = b.lower().split()
    if len(words_b) < n:
        return False
    ngrams_b = set(" ".join(words_b[i:i + n]) for i in range(len(words_b) - n + 1))
    return bool(set(ngrams) & ngrams_b)


def _make_id() -> str:
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    h = hashlib.md5(ts.encode()).hexdigest()[:4]
    return f"gp_{ts}_{h}"


# ── Directory ─────────────────────────────────────────────────────────────────

def get_global_dir() -> Path:
    """Return ~/.unicode/global/, creating it if absent."""
    d = Path.home() / ".unicode" / "global"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _yaml_path() -> Path:
    return get_global_dir() / "global_patterns.yaml"


def _md_path() -> Path:
    return get_global_dir() / "global_wisdom.md"


# ── Read ──────────────────────────────────────────────────────────────────────

def _load_all() -> list[dict]:
    p = _yaml_path()
    if not p.exists():
        return []
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def load_global_patterns(task: str, n: int = 8) -> list[dict]:
    """Return up to *n* global patterns most relevant to *task*.

    BM25Plus scoring over ``pattern + context`` corpus, with quality weighting.
    Returns [] if the store is empty or the library is unavailable.
    """
    entries = _load_all()
    if not entries:
        return []

    corpus = [_tokenize(e.get("pattern", "") + " " + e.get("context", "")) for e in entries]
    if not any(corpus):
        return []

    bm25 = BM25Plus(corpus)
    query_tokens = _tokenize(task)
    if not query_tokens:
        return entries[:n]

    raw_scores = bm25.get_scores(query_tokens)
    ranked = []
    for entry, raw in zip(entries, raw_scores):
        if raw <= 0:
            continue
        q = entry.get("quality_score", 0.5)
        final = raw * (0.4 + 0.6 * q)
        ranked.append((final, entry))

    ranked.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in ranked[:n]]


def format_global_context(patterns: list[dict]) -> str:
    """Format a list of global patterns into a prompt-ready string."""
    if not patterns:
        return ""

    by_cat: dict[str, list[dict]] = {}
    for p in patterns:
        cat = p.get("category", "general")
        by_cat.setdefault(cat, []).append(p)

    lines = ["## Cross-Project Patterns (learned from previous projects)"]
    char_budget = 800
    used = len(lines[0])

    for cat, entries in sorted(by_cat.items()):
        for e in entries:
            pattern = e.get("pattern", "")
            context = e.get("context", "")
            line = f"[{cat}] {pattern}"
            detail = f"  ->{context}" if context else ""
            chunk = line + ("\n" + detail if detail else "")
            if used + len(chunk) > char_budget:
                # Truncate context to fit
                remaining = char_budget - used - len(line) - 6
                if remaining > 20 and detail:
                    chunk = line + f"\n  ->{context[:remaining]}…"
                else:
                    chunk = line
            lines.append(chunk)
            used += len(chunk)
            if used >= char_budget:
                break
        if used >= char_budget:
            break

    return "\n".join(lines)


# ── Write ─────────────────────────────────────────────────────────────────────

def write_global_patterns(new_entries: list[dict], source_project: str = "") -> None:
    """Append new entries to the global store, deduplicating by pattern text.

    Entries that share 5+ consecutive words with an existing pattern are
    treated as duplicates — the existing entry's usage_count is bumped instead.
    """
    if not new_entries:
        return

    existing = _load_all()
    existing_patterns = [e.get("pattern", "") for e in existing]

    added = []
    for entry in new_entries:
        pattern_text = entry.get("pattern", "").strip()
        if not pattern_text:
            continue

        # Dedup check
        dup_idx = next(
            (i for i, ep in enumerate(existing_patterns)
             if _share_n_consecutive_words(pattern_text, ep)),
            None,
        )
        if dup_idx is not None:
            existing[dup_idx]["usage_count"] = existing[dup_idx].get("usage_count", 0) + 1
            continue

        new_entry = {
            "id": _make_id(),
            "source_project": source_project or "",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "category": entry.get("category", "general"),
            "pattern": pattern_text,
            "context": entry.get("context", ""),
            "quality_score": round(float(entry.get("quality_score", 0.5)), 3),
            "usage_count": 0,
        }
        existing.append(new_entry)
        existing_patterns.append(pattern_text)
        added.append(new_entry)

    p = _yaml_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(existing, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    if added:
        _append_wisdom_md(added)


def _append_wisdom_md(entries: list[dict]) -> None:
    """Append new entries to the human-readable global_wisdom.md."""
    p = _md_path()
    if not p.exists():
        p.write_text("# Global Wisdom\n\nCross-project patterns learned by the AI Orchestrator.\n\n", encoding="utf-8")

    lines = []
    for e in entries:
        cat = e.get("category", "general")
        pattern = e.get("pattern", "")
        context = e.get("context", "")
        date = e.get("date", "")
        lines.append(f"\n### {cat.title()}")
        lines.append(f"- [{date}] {pattern}")
        if context:
            lines.append(f"  - {context}")

    with open(p, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── Usage tracking ────────────────────────────────────────────────────────────

def increment_usage_counts(ids: list[str]) -> None:
    """Bump usage_count on the given pattern IDs."""
    if not ids:
        return
    entries = _load_all()
    if not entries:
        return
    id_set = set(ids)
    modified = False
    for e in entries:
        if e.get("id") in id_set:
            e["usage_count"] = e.get("usage_count", 0) + 1
            modified = True
    if modified:
        p = _yaml_path()
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(entries, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
