"""Global agent fallback chain for usage-limit recovery.

When an agent hits its usage/rate limit, the orchestrator uses this module
to find the next available agent in the chain and hand off remaining work.

Chain order (most capable → last resort): Claude → Codex → Qwen
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.base import BaseAgent

FALLBACK_CHAIN: list[str] = ["claude", "codex", "qwen"]


def get_fallback_agent(
    current_agent_name: str,
    agents: dict[str, BaseAgent],
) -> BaseAgent | None:
    """Return the next available agent after *current_agent_name* in the chain.

    *agents* maps lowercase agent name to agent instance.
    Returns None if no fallback is available.
    """
    current_lower = current_agent_name.lower()
    try:
        idx = FALLBACK_CHAIN.index(current_lower)
    except ValueError:
        idx = -1
    for candidate in FALLBACK_CHAIN[idx + 1:]:
        agent = agents.get(candidate)
        if agent is not None:
            return agent
    return None


def build_agents_dict(claude, codex, qwen) -> dict[str, BaseAgent]:
    """Build the standard agents dict used by get_fallback_agent."""
    result: dict[str, BaseAgent] = {}
    if claude is not None:
        result["claude"] = claude
    if codex is not None:
        result["codex"] = codex
    if qwen is not None:
        result["qwen"] = qwen
    return result
