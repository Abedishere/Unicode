"""End-to-end smoke test for the AI orchestrator pipeline.

Follows the pattern from test_runner.py: no framework, plain asserts,
pass/fail counter, finally cleanup.

Stubs all three agent CLIs so the test runs without API keys or external
tools. The stub Claude agent writes calc.py on its first implement() call.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# ── Stub agents ───────────────────────────────────────────────────────────────

from agents.base import BaseAgent
from agents.qwen_agent import QwenAgent


class _ScriptedMixin:
    """Shared scripted-response logic for all stub agents."""

    def _init_scripted(self, responses: list[str]) -> None:
        self._responses = responses
        self._call_count = 0

    def _next(self) -> str:
        resp = self._responses[min(self._call_count, len(self._responses) - 1)]
        self._call_count += 1
        return resp


class StubClaudeAgent(_ScriptedMixin, BaseAgent):
    """Scripted stub for ClaudeAgent (admin + developer roles)."""

    def __init__(self, responses: list[str], work_dir: str):
        BaseAgent.__init__(self, model="stub", timeout=30, working_dir=work_dir)
        self._init_scripted(responses)
        self.dev_model = "stub"

    @property
    def name(self) -> str:
        return "Claude"

    def query(self, prompt: str) -> str:
        self._maybe_audit(prompt)
        return self._next()

    def implement(self, plan: str) -> str:
        calc_path = Path(self.working_dir) / "calc.py"
        calc_path.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        return "Implemented calc.py with add(a, b) function."

    def implement_interactive(self, task: str, plan: str) -> int:
        calc_path = Path(self.working_dir) / "calc.py"
        calc_path.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        return 0


class StubCodexAgent(_ScriptedMixin, BaseAgent):
    """Scripted stub for CodexAgent (planning + review roles)."""

    def __init__(self, responses: list[str], work_dir: str):
        BaseAgent.__init__(self, model="stub", timeout=30, working_dir=work_dir)
        self._init_scripted(responses)
        self.dev_model = "stub"

    @property
    def name(self) -> str:
        return "Codex"

    def query(self, prompt: str) -> str:
        self._maybe_audit(prompt)
        return self._next()

    def review_query(self, prompt: str) -> str:
        return "APPROVED\n\nThe implementation looks correct."


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


# ── Test ──────────────────────────────────────────────────────────────────────

_STRUCTURED_PLAN = """\
## Shared Dependencies
None

## Files

### calc.py (CREATE)
- Implement add(a, b) function that returns a + b
"""

_CLAUDE_RESPONSES = [
    # Discussion rounds (Codex goes first, Claude responds)
    "I agree we should implement a simple add(a, b) function in calc.py. AGREED",
    # Any subsequent admin queries
    "APPROVED",
]

_CODEX_RESPONSES = [
    # Research queries (codex_a, codex_b)
    "Common approach: simple pure functions, no external dependencies.",
    "Python add function is straightforward. No known pitfalls.",
    # Discussion round (Codex goes first)
    "We should create calc.py with an add(a, b) function. AGREED",
    # Plan
    _STRUCTURED_PLAN,
    # Commit message
    "add calc.py with add function",
    # agent_update_md and any other queries
    "Updated.",
]

_QWEN_RESPONSES = [
    # research_query
    "Architectural pattern: simple utility modules.",
    # write_orchestrator_md summary
    "Created calc.py with add(a, b).",
    # _synthesize_memory query
    "{}",
    # _compact_memory_files or any remaining queries
    "No compaction needed.",
]


def test_e2e_smoke():
    """Full pipeline smoke test: research → discuss → plan → implement → review → memory."""
    print("\n=== E2E SMOKE TEST: full pipeline ===\n")

    tmp = tempfile.mkdtemp(prefix="orch_e2e_")
    work_dir = tmp

    import utils.approval as _approval
    import orchestrator as _orch
    _orig_req = _approval.request_approval

    try:
        # Bootstrap a git repo so git operations don't fail
        from utils.git_utils import init_repo
        init_repo(work_dir)

        # Build stub agents
        claude = StubClaudeAgent(_CLAUDE_RESPONSES, work_dir)
        codex  = StubCodexAgent(_CODEX_RESPONSES, work_dir)
        qwen   = StubQwenAgent(_QWEN_RESPONSES, work_dir)

        # Minimal config — auto-approve all, no git commit, fast timeouts
        cfg = {
            "auto_approve_all": True,
            "auto_commit": False,
            "timeout_seconds": 30,
            "research_wall_seconds": 10,
            "max_discussion_rounds": 2,
            "max_review_iterations": 1,
            "discussion_rounds": 2,
            "allow_user_questions": False,
            "repo_map_max_tokens": 500,
            "tiers": {},
        }

        # Auto-approve all phases; patch git-commit to deny so no prompt blocks the test
        def _patched_req(action: str, description: str):
            if action == "git-commit":
                return "deny", None
            return _orig_req(action, description)

        _approval.set_auto_all(True)
        _approval.request_approval = _patched_req
        _orch.request_approval = _patched_req

        _orch._run_task(
            task="Create a calc.py module with an add(a, b) function",
            cfg=cfg,
            work_dir=work_dir,
            claude=claude,
            codex=codex,
            qwen=qwen,
            phase="all",
            tier="standard",
        )

        # ── Assertion 1: calc.py contains def add(a, b) ──────────────────────
        calc_path = Path(work_dir) / "calc.py"
        assert calc_path.exists(), f"calc.py not found in {work_dir}"
        calc_content = calc_path.read_text(encoding="utf-8")
        assert "def add(a, b)" in calc_content, (
            f"def add(a, b) not found in calc.py: {calc_content!r}"
        )
        print("PASS: calc.py contains def add(a, b)")

        # ── Assertion 2: memory.yaml exists and is a valid dict ──────────────
        from utils.memory import load_memory
        memory_path = Path(work_dir) / ".orchestrator" / "memory.yaml"
        assert memory_path.exists(), f"memory.yaml not found at {memory_path}"
        memory = load_memory(work_dir)
        assert isinstance(memory, dict), f"memory.yaml is not a dict: {type(memory)}"
        print("PASS: .orchestrator/memory.yaml exists and is a valid dict")

        # ── Assertion 3: task_index has ≥ 1 entry ────────────────────────────
        task_index = memory.get("task_index", [])
        assert len(task_index) >= 1, (
            f"memory['task_index'] has {len(task_index)} entries, expected ≥ 1"
        )
        print(f"PASS: memory['task_index'] has {len(task_index)} entry/entries")

    finally:
        # Always restore approval state and clean up temp dir
        _approval.request_approval = _orig_req
        _orch.request_approval = _orig_req
        _approval.set_auto_all(False)
        _cleanup(work_dir)


def _cleanup(tmp_dir: str) -> None:
    import shutil
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


if __name__ == "__main__":
    tests = [test_e2e_smoke]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"\nFAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"\nFAIL (unexpected error): {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
