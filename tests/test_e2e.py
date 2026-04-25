"""End-to-end smoke test: full orchestrator pipeline with stub agents."""
from __future__ import annotations

from pathlib import Path


import utils.approval as _approval
import orchestrator as _orch
from tests.conftest import StubClaudeAgent, StubCodexAgent, StubKiroAgent
from utils.memory import load_memory

_STRUCTURED_PLAN = """\
## Shared Dependencies
None

## Files

### calc.py (CREATE)
- Implement add(a, b) function that returns a + b
"""

_CLAUDE_RESPONSES = [
    "I agree we should implement a simple add(a, b) function in calc.py. AGREED",
    "APPROVED",
]

_CODEX_RESPONSES = [
    "Common approach: simple pure functions, no external dependencies.",
    "Python add function is straightforward. No known pitfalls.",
    "We should create calc.py with an add(a, b) function. AGREED",
    _STRUCTURED_PLAN,
    "add calc.py with add function",
    "Updated.",
]

_KIRO_RESPONSES = [
    "Architectural pattern: simple utility modules.",
    "Created calc.py with add(a, b).",
    "{}",
    "No compaction needed.",
]


def test_basic_implement_flow(
    work_dir: str, base_cfg: dict, patch_internal_agents
) -> None:
    """research → discuss → plan → implement → review → memory completes cleanly."""
    orig_req = _approval.request_approval

    def _patched_req(action: str, description: str):
        if action == "git-commit":
            return "deny", None
        return orig_req(action, description)

    claude = StubClaudeAgent(_CLAUDE_RESPONSES[:], work_dir)
    codex = StubCodexAgent(_CODEX_RESPONSES[:], work_dir)
    kiro = StubKiroAgent(_KIRO_RESPONSES[:], work_dir)

    _approval.set_auto_all(True)
    _approval.request_approval = _patched_req
    _orch.request_approval = _patched_req
    try:
        _orch._run_task(
            task="Create a calc.py module with an add(a, b) function",
            cfg=base_cfg,
            work_dir=work_dir,
            claude=claude,
            codex=codex,
            kiro=kiro,
            phase="all",
            tier="standard",
        )
    finally:
        _approval.request_approval = orig_req
        _orch.request_approval = orig_req
        _approval.set_auto_all(False)

    # calc.py must exist and contain the expected function
    calc = Path(work_dir) / "calc.py"
    assert calc.exists(), "calc.py was not created"
    assert "def add(a, b)" in calc.read_text(encoding="utf-8")

    # memory.yaml must exist and be a valid dict with at least one task
    mem = load_memory(work_dir)
    assert isinstance(mem, dict)
    assert len(mem.get("task_index", [])) >= 1
