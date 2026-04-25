"""Unit tests for the agent fallback chain in utils/fallback.py."""
from __future__ import annotations

from unittest.mock import MagicMock

from utils.fallback import FALLBACK_CHAIN, build_agents_dict, get_fallback_agent


def _mock(name: str) -> MagicMock:
    a = MagicMock()
    a.name = name
    return a


def test_chain_order() -> None:
    assert FALLBACK_CHAIN == ["claude", "codex", "kiro"]


def test_claude_falls_back_to_codex() -> None:
    agents = build_agents_dict(_mock("Claude"), _mock("Codex"), _mock("Kiro"))
    assert get_fallback_agent("claude", agents) is agents["codex"]


def test_codex_falls_back_to_kiro() -> None:
    agents = build_agents_dict(_mock("Claude"), _mock("Codex"), _mock("Kiro"))
    assert get_fallback_agent("codex", agents) is agents["kiro"]


def test_kiro_returns_none() -> None:
    agents = build_agents_dict(_mock("Claude"), _mock("Codex"), _mock("Kiro"))
    assert get_fallback_agent("kiro", agents) is None


def test_fallback_skips_absent_codex() -> None:
    """If Codex is absent, Claude falls back directly to Kiro."""
    agents = build_agents_dict(_mock("Claude"), None, _mock("Kiro"))
    assert get_fallback_agent("claude", agents) is agents["kiro"]


def test_unknown_agent_starts_from_beginning() -> None:
    """An unrecognised agent name falls back to the first chain entry."""
    agents = build_agents_dict(_mock("Claude"), _mock("Codex"), _mock("Kiro"))
    result = get_fallback_agent("unknown", agents)
    assert result is agents["claude"]


def test_build_agents_dict_excludes_none() -> None:
    agents = build_agents_dict(_mock("Claude"), None, None)
    assert "claude" in agents
    assert "codex" not in agents
    assert "kiro" not in agents
