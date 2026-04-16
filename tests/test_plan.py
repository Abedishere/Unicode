"""Unit tests for phases/plan.py — consolidate_plan scenarios."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from phases.plan import consolidate_plan


_STRUCTURED_PLAN = """\
## Shared Dependencies
None

## Files

### auth.py (CREATE)
- Implement login(user, password) function
"""

_UNSTRUCTURED_PLAN = "Just write the auth module however you want."


def _mock_codex(responses: list[str]) -> MagicMock:
    codex = MagicMock()
    codex.name = "Codex"
    call_count = [0]

    def query(prompt: str) -> str:
        resp = responses[min(call_count[0], len(responses) - 1)]
        call_count[0] += 1
        return resp

    codex.query.side_effect = query
    return codex


def _mock_claude(response: str) -> MagicMock:
    claude = MagicMock()
    claude.name = "Claude"
    claude.query.return_value = response
    return claude


# ── Basic flow ────────────────────────────────────────────────────────────────

def test_consolidate_plan_returns_string(tmp_path) -> None:
    codex = _mock_codex([_STRUCTURED_PLAN])
    result = consolidate_plan("build auth", codex, str(tmp_path))
    assert isinstance(result, str)
    assert len(result) > 0


def test_consolidate_plan_structured_plan_no_retry(tmp_path) -> None:
    """Structured plan on first try — should not query codex a second time."""
    codex = _mock_codex([_STRUCTURED_PLAN])
    consolidate_plan("build auth", codex, str(tmp_path))
    assert codex.query.call_count == 1


def test_consolidate_plan_returns_codex_plan(tmp_path) -> None:
    codex = _mock_codex([_STRUCTURED_PLAN])
    result = consolidate_plan("build auth", codex, str(tmp_path))
    assert "auth.py" in result


# ── Empty plan ────────────────────────────────────────────────────────────────

def test_consolidate_plan_empty_codex_response(tmp_path) -> None:
    """Empty plan triggers a warning but still returns (empty) string."""
    codex = _mock_codex([""])
    result = consolidate_plan("task", codex, str(tmp_path))
    assert isinstance(result, str)


# ── Unstructured plan with retry ──────────────────────────────────────────────

def test_consolidate_plan_unstructured_triggers_retry(tmp_path) -> None:
    """Unstructured first plan → retry query fired."""
    codex = _mock_codex([_UNSTRUCTURED_PLAN, _STRUCTURED_PLAN])
    result = consolidate_plan("build auth", codex, str(tmp_path))
    assert codex.query.call_count == 2
    assert "auth.py" in result  # retry succeeded


def test_consolidate_plan_retry_also_unstructured(tmp_path) -> None:
    """Both attempts unstructured → fallback to first (unstructured) plan."""
    codex = _mock_codex([_UNSTRUCTURED_PLAN, _UNSTRUCTURED_PLAN])
    result = consolidate_plan("task", codex, str(tmp_path))
    # Falls back to the original unstructured plan
    assert isinstance(result, str)


def test_consolidate_plan_retry_exception_falls_back(tmp_path) -> None:
    """If retry call raises, fall back to the original plan."""
    codex = MagicMock()
    codex.name = "Codex"
    call_count = [0]

    def query(prompt: str) -> str:
        call_count[0] += 1
        if call_count[0] == 1:
            return _UNSTRUCTURED_PLAN
        raise RuntimeError("network error")

    codex.query.side_effect = query
    result = consolidate_plan("task", codex, str(tmp_path))
    assert isinstance(result, str)  # did not raise


# ── Claude synthesis ──────────────────────────────────────────────────────────

def test_consolidate_plan_claude_synthesis_replaces_codex_plan(tmp_path) -> None:
    """When Claude returns a structured plan, it replaces Codex's plan."""
    claude_plan = """\
## Shared Dependencies
None

## Files

### auth.py (CREATE)
- Claude's improved spec
"""
    codex = _mock_codex([_STRUCTURED_PLAN])
    claude = _mock_claude(claude_plan)
    result = consolidate_plan("build auth", codex, str(tmp_path), claude=claude)
    assert "Claude's improved spec" in result


def test_consolidate_plan_claude_unstructured_keeps_codex(tmp_path) -> None:
    """If Claude returns an unstructured response, Codex's plan is kept."""
    codex = _mock_codex([_STRUCTURED_PLAN])
    claude = _mock_claude("I think the plan looks good overall.")
    result = consolidate_plan("task", codex, str(tmp_path), claude=claude)
    # Codex's plan is kept
    assert "auth.py" in result


def test_consolidate_plan_claude_exception_keeps_codex(tmp_path) -> None:
    """If Claude raises, Codex's plan is still returned."""
    codex = _mock_codex([_STRUCTURED_PLAN])
    claude = MagicMock()
    claude.name = "Claude"
    claude.query.side_effect = RuntimeError("Claude unavailable")
    result = consolidate_plan("task", codex, str(tmp_path), claude=claude)
    assert "auth.py" in result


def test_consolidate_plan_without_claude(tmp_path) -> None:
    """No Claude provided — only Codex plan is returned."""
    codex = _mock_codex([_STRUCTURED_PLAN])
    result = consolidate_plan("task", codex, str(tmp_path), claude=None)
    assert "auth.py" in result


# ── Optional parameters ───────────────────────────────────────────────────────

def test_consolidate_plan_with_skills_context(tmp_path) -> None:
    codex = _mock_codex([_STRUCTURED_PLAN])
    result = consolidate_plan(
        "task", codex, str(tmp_path), skills_context="Use pytest for testing."
    )
    assert isinstance(result, str)
    # Skills context is injected into the prompt — verify codex was called
    assert codex.query.call_count >= 1


def test_consolidate_plan_with_repo_map(tmp_path) -> None:
    codex = _mock_codex([_STRUCTURED_PLAN])
    result = consolidate_plan(
        "task", codex, str(tmp_path), repo_map="PROJECT STRUCTURE:\n  app.py"
    )
    assert isinstance(result, str)
