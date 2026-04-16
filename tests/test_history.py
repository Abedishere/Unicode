"""Unit tests for utils/history.py."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


from utils.history import (
    _AGENTS_MD_HEADER,
    _CLAUDE_MD_HEADER,
    _MAX_BODY_WORDS,
    _enforce_word_limit,
    agent_update_md,
    append_history,
    init_agent_md,
    write_orchestrator_md,
)


# ── _enforce_word_limit ───────────────────────────────────────────────────────

def test_enforce_word_limit_short_text_unchanged() -> None:
    text = "short text here"
    assert _enforce_word_limit(text, max_words=100) == text


def test_enforce_word_limit_exact_boundary_unchanged() -> None:
    text = " ".join(f"word{i}" for i in range(10))
    assert _enforce_word_limit(text, max_words=10) == text


def test_enforce_word_limit_trims_long_text() -> None:
    text = " ".join(f"word{i}" for i in range(50))
    result = _enforce_word_limit(text, max_words=10)
    assert "*(body trimmed" in result


def test_enforce_word_limit_preserves_newline_boundary() -> None:
    text = "line one word1 word2\nline two word3 word4\nline three word5"
    result = _enforce_word_limit(text, max_words=4)
    # Cut should happen at a newline, not mid-word
    assert "*(body trimmed" in result
    assert "\n" in result or result.endswith("*(body trimmed to stay within the 400-word limit)*")


def test_enforce_word_limit_uses_default_max() -> None:
    text = " ".join(f"w{i}" for i in range(_MAX_BODY_WORDS + 1))
    result = _enforce_word_limit(text)
    assert "*(body trimmed" in result


def test_enforce_word_limit_no_newlines() -> None:
    """Handles text without newlines — cuts at word boundary."""
    text = " ".join(f"w{i}" for i in range(20))
    result = _enforce_word_limit(text, max_words=5)
    assert "*(body trimmed" in result


# ── init_agent_md ─────────────────────────────────────────────────────────────

def test_init_agent_md_creates_files(tmp_path: Path) -> None:
    init_agent_md(str(tmp_path))
    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / "AGENTS.md").exists()


def test_init_agent_md_writes_correct_headers(tmp_path: Path) -> None:
    init_agent_md(str(tmp_path))
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8").startswith(
        "# Project Context (Claude Code)"
    )
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8").startswith(
        "# Project Context (Codex)"
    )


def test_init_agent_md_idempotent(tmp_path: Path) -> None:
    """Calling init_agent_md twice does not overwrite existing content."""
    (tmp_path / "CLAUDE.md").write_text("custom content", encoding="utf-8")
    init_agent_md(str(tmp_path))
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "custom content"


# ── append_history ────────────────────────────────────────────────────────────

def test_append_history_creates_file(tmp_path: Path) -> None:
    append_history(str(tmp_path), "build auth", "completed", 120.0, "- wrote auth.py", "t.log")
    assert (tmp_path / ".orchestrator" / "history.md").exists()


def test_append_history_content(tmp_path: Path) -> None:
    append_history(str(tmp_path), "build auth", "completed", 90.5, "- wrote file", "t.log")
    content = (tmp_path / ".orchestrator" / "history.md").read_text(encoding="utf-8")
    assert "build auth" in content
    assert "completed" in content
    assert "t.log" in content


def test_append_history_appends_multiple(tmp_path: Path) -> None:
    append_history(str(tmp_path), "task A", "done", 10.0, "- a", "a.log")
    append_history(str(tmp_path), "task B", "done", 20.0, "- b", "b.log")
    content = (tmp_path / ".orchestrator" / "history.md").read_text(encoding="utf-8")
    assert "task A" in content
    assert "task B" in content


# ── agent_update_md ───────────────────────────────────────────────────────────

def _stub_agent(response: str) -> MagicMock:
    agent = MagicMock()
    agent.name = "StubAgent"
    agent.query.return_value = response
    return agent


def test_agent_update_md_writes_file(tmp_path: Path) -> None:
    agent = _stub_agent("Here are the key facts about this project.")
    agent_update_md(str(tmp_path), "build auth", "plan text", [], agent, "CLAUDE.md")
    content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "key facts" in content


def test_agent_update_md_preserves_header(tmp_path: Path) -> None:
    agent = _stub_agent("Some body content.")
    agent_update_md(str(tmp_path), "task", "plan", [], agent, "CLAUDE.md")
    content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert content.startswith(_CLAUDE_MD_HEADER)


def test_agent_update_md_trims_long_response(tmp_path: Path) -> None:
    long_body = " ".join(f"word{i}" for i in range(_MAX_BODY_WORDS + 50))
    agent = _stub_agent(long_body)
    agent_update_md(str(tmp_path), "task", "plan", [], agent, "CLAUDE.md")
    content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "*(body trimmed" in content


def test_agent_update_md_skips_empty_response(tmp_path: Path) -> None:
    agent = _stub_agent("")
    agent_update_md(str(tmp_path), "task", "plan", [], agent, "CLAUDE.md")
    # File should not be created if agent returns empty
    assert not (tmp_path / "CLAUDE.md").exists()


def test_agent_update_md_handles_exception(tmp_path: Path) -> None:
    agent = MagicMock()
    agent.name = "StubAgent"
    agent.query.side_effect = RuntimeError("API down")
    # Should not raise — exception is caught internally
    agent_update_md(str(tmp_path), "task", "plan", [], agent, "CLAUDE.md")


def test_agent_update_md_agents_md(tmp_path: Path) -> None:
    agent = _stub_agent("Codex project context.")
    agent_update_md(str(tmp_path), "task", "plan", [], agent, "AGENTS.md")
    content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert content.startswith(_AGENTS_MD_HEADER)


# ── write_orchestrator_md ─────────────────────────────────────────────────────

def test_write_orchestrator_md_creates_file(tmp_path: Path) -> None:
    agent = _stub_agent("# Project\nThis is the project summary.")
    write_orchestrator_md(str(tmp_path), "build auth", "plan", [], agent)
    assert (tmp_path / "orchestrator.md").exists()


def test_write_orchestrator_md_content(tmp_path: Path) -> None:
    agent = _stub_agent("# Project\nSummary here.")
    write_orchestrator_md(str(tmp_path), "task", "plan", [], agent)
    content = (tmp_path / "orchestrator.md").read_text(encoding="utf-8")
    assert "Summary here" in content


def test_write_orchestrator_md_exception_skipped(tmp_path: Path) -> None:
    agent = MagicMock()
    agent.name = "Qwen"
    agent.query.side_effect = RuntimeError("Qwen unavailable")
    # Should not raise — exception is logged and skipped
    write_orchestrator_md(str(tmp_path), "task", "plan", [], agent)
    assert not (tmp_path / "orchestrator.md").exists()
