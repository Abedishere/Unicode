"""Global agent fallback chain for usage-limit recovery.

When an agent hits its usage/rate limit, the orchestrator uses this module
to find the next available agent in the chain and hand off remaining work.

Chain order (most capable → last resort): Claude → Codex → Kiro
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.base import BaseAgent

FALLBACK_CHAIN: list[str] = ["claude", "codex", "kiro"]


def _canonical_agent_name(agent_name: str) -> str:
    """Map display names like ``Claude (dev:sonnet)`` to fallback keys."""
    current_lower = agent_name.lower().strip()
    for candidate in FALLBACK_CHAIN:
        if current_lower == candidate or current_lower.startswith(f"{candidate} "):
            return candidate
    return current_lower


def get_fallback_agent(
    current_agent_name: str,
    agents: dict[str, BaseAgent],
) -> BaseAgent | None:
    """Return the next available agent after *current_agent_name* in the chain.

    *agents* maps lowercase agent name to agent instance.
    Returns None if no fallback is available.
    """
    current_lower = _canonical_agent_name(current_agent_name)
    try:
        idx = FALLBACK_CHAIN.index(current_lower)
    except ValueError:
        idx = -1
    for candidate in FALLBACK_CHAIN[idx + 1:]:
        agent = agents.get(candidate)
        if agent is not None:
            return agent
    return None


def build_agents_dict(claude, codex, kiro) -> dict[str, BaseAgent]:
    """Build the standard agents dict used by get_fallback_agent."""
    result: dict[str, BaseAgent] = {}
    if claude is not None:
        result["claude"] = claude
    if codex is not None:
        result["codex"] = codex
    if kiro is not None:
        result["kiro"] = kiro
    return result
