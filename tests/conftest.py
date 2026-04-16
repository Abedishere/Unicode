"""Shared fixtures and stub agents for the ai-orchestrator test suite."""
from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest

from agents.base import BaseAgent
from agents.qwen_agent import QwenAgent
from utils.runner import UsageLimitReached


# ── Stub agent helpers ────────────────────────────────────────────────────────

class _ScriptedMixin:
    """Provides scripted response cycling for stub agents."""

    def _init_scripted(self, responses: list[str]) -> None:
        self._responses = responses
        self._call_count = 0

    def _next(self) -> str:
        resp = self._responses[min(self._call_count, len(self._responses) - 1)]
        self._call_count += 1
        return resp


class StubClaudeAgent(_ScriptedMixin, BaseAgent):
    """Scripted stub for ClaudeAgent (admin + developer roles)."""

    def __init__(
        self,
        responses: list[str],
        work_dir: str,
        *,
        fail_implement: bool = False,
        write_filename: str = "calc.py",
    ):
        BaseAgent.__init__(self, model="stub", timeout=30, working_dir=work_dir)
        self._init_scripted(responses)
        self.dev_model = "stub"
        self._fail_implement = fail_implement
        self._write_filename = write_filename

    @property
    def name(self) -> str:
        return "Claude"

    def query(self, prompt: str) -> str:
        self._maybe_audit(prompt)
        return self._next()

    def implement(self, plan: str) -> str:
        if self._fail_implement:
            raise UsageLimitReached("Claude", "simulated limit in tests")
        path = Path(self.working_dir) / self._write_filename
        path.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        return f"Implemented {self._write_filename}."

    def implement_interactive(self, task: str, plan: str) -> int:
        if self._fail_implement:
            raise UsageLimitReached("Claude", "simulated limit in tests")
        path = Path(self.working_dir) / self._write_filename
        path.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        return 0


class StubCodexAgent(_ScriptedMixin, BaseAgent):
    """Scripted stub for CodexAgent (planning + review roles)."""

    def __init__(
        self,
        responses: list[str],
        work_dir: str,
        *,
        write_filename: str | None = None,
    ):
        BaseAgent.__init__(self, model="stub", timeout=30, working_dir=work_dir)
        self._init_scripted(responses)
        self.dev_model = "stub"
        self._write_filename = write_filename

    @property
    def name(self) -> str:
        return "Codex"

    def query(self, prompt: str) -> str:
        self._maybe_audit(prompt)
        return self._next()

    def review_query(self, prompt: str) -> str:
        return "APPROVED\n\nThe implementation looks correct."

    def implement(self, plan: str) -> str:
        if self._write_filename:
            path = Path(self.working_dir) / self._write_filename
            path.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        return "Codex fallback: implemented successfully."


class StubQwenAgent(_ScriptedMixin, QwenAgent):
    """Scripted stub for QwenAgent (research + memory synthesis roles)."""

    def __init__(self, responses: list[str], work_dir: str):
        QwenAgent.__init__(self, model="stub", timeout=30, working_dir=work_dir)
        self._init_scripted(responses)

    def query(self, prompt: str) -> str:
        self._maybe_audit(prompt)
        return self._next()

    def research_query(self, prompt: str) -> str:
        return self._next()


# ── Standard canned response libraries ───────────────────────────────────────

_STRUCTURED_PLAN = """\
## Shared Dependencies
None

## Files

### calc.py (CREATE)
- Implement add(a, b) function that returns a + b
"""

CLAUDE_RESPONSES = [
    "I agree we should implement a simple add(a, b) function in calc.py. AGREED",
    "APPROVED",
]

CODEX_RESPONSES = [
    "Common approach: simple pure functions, no external dependencies.",
    "Python add function is straightforward. No known pitfalls.",
    "We should create calc.py with an add(a, b) function. AGREED",
    _STRUCTURED_PLAN,
    "add calc.py with add function",
    "Updated.",
]

QWEN_RESPONSES = [
    "Architectural pattern: simple utility modules.",
    "Created calc.py with add(a, b).",
    "{}",
    "No compaction needed.",
]


# ── Pytest fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def work_dir(tmp_path: Path) -> Generator[str, None, None]:
    """Temporary git-initialised working directory, cleaned up automatically."""
    from utils.git_utils import init_repo
    wd = str(tmp_path)
    init_repo(wd)
    yield wd


@pytest.fixture
def stub_agents(work_dir: str):
    """Return (claude, codex, qwen) stub agents wired to work_dir."""
    claude = StubClaudeAgent(CLAUDE_RESPONSES[:], work_dir)
    codex = StubCodexAgent(CODEX_RESPONSES[:], work_dir)
    qwen = StubQwenAgent(QWEN_RESPONSES[:], work_dir)
    return claude, codex, qwen


@pytest.fixture
def base_cfg() -> dict:
    """Minimal orchestrator config for fast, deterministic test runs."""
    return {
        # Gate / approval
        "auto_approve_all": True,
        "auto_commit": False,
        "allow_user_questions": False,
        # Models (stubs — no real CLI calls needed)
        "claude_model": "stub",
        "dev_model": "stub",
        "codex_model": "stub",
        "qwen_model": "stub",
        # Timeouts
        "timeout_seconds": 30,
        "codex_timeout_seconds": 60,
        "research_wall_seconds": 10,
        # Pipeline limits
        "discussion_rounds": 2,
        "max_discussion_rounds": 2,
        "max_review_iterations": 1,
        "discussion_summary_window": 2,
        # Misc
        "repo_map_max_tokens": 500,
        "file_by_file_generation": True,
        "tiers": {},
    }


@pytest.fixture
def patch_internal_agents():
    """Patch ClaudeAgent/QwenAgent constructors in orchestrator to return stubs.

    The research phase creates its own synthesizer (ClaudeAgent) and qwen_scout
    (QwenAgent) internally.  This fixture replaces those with MagicMocks so no
    real CLI calls are made during integration tests.
    """
    from unittest.mock import MagicMock, patch

    def _factory(*args, **kwargs):
        m = MagicMock()
        m.name = "InternalStub"
        m.query.return_value = "stub context"
        m.research_query.return_value = "stub research"
        return m

    with (
        patch("orchestrator.ClaudeAgent", side_effect=_factory),
        patch("orchestrator.QwenAgent", side_effect=_factory),
    ):
        yield
