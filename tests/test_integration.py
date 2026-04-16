"""Integration tests: targeted pipeline scenarios using stub agents."""
from __future__ import annotations

from pathlib import Path


import utils.approval as _approval
import orchestrator as _orch
from tests.conftest import (
    QWEN_RESPONSES,
    StubClaudeAgent,
    StubCodexAgent,
    StubQwenAgent,
)
from utils.memory import load_memory


def _patch_approval(orig):
    """Patch approval to auto-approve everything except git-commit."""
    def _req(action: str, description: str):
        if action == "git-commit":
            return "deny", None
        return orig(action, description)
    return _req


# ── helpers ───────────────────────────────────────────────────────────────────

def _run(task, cfg, work_dir, claude, codex, qwen, phase="all"):
    orig = _approval.request_approval
    _approval.set_auto_all(True)
    _approval.request_approval = _patch_approval(orig)
    _orch.request_approval = _patch_approval(orig)
    try:
        _orch._run_task(
            task=task,
            cfg=cfg,
            work_dir=work_dir,
            claude=claude,
            codex=codex,
            qwen=qwen,
            phase=phase,
            tier="standard",
        )
    finally:
        _approval.request_approval = orig
        _orch.request_approval = orig
        _approval.set_auto_all(False)


# ── tests ─────────────────────────────────────────────────────────────────────

def test_memory_files_exist_after_pipeline(
    work_dir: str, base_cfg: dict, patch_internal_agents
) -> None:
    """All five .orchestrator memory files are created after a full pipeline run."""
    _PLAN = """\
## Shared Dependencies
None

## Files

### calc.py (CREATE)
- Implement add(a, b) function
"""
    claude = StubClaudeAgent(
        ["I agree. AGREED", "APPROVED"],
        work_dir,
    )
    codex = StubCodexAgent(
        [
            "No pitfalls.",
            "No pitfalls.",
            "We should add calc.py. AGREED",
            _PLAN,
            "add calc.py",
            "Updated.",
        ],
        work_dir,
    )
    qwen = StubQwenAgent(QWEN_RESPONSES[:], work_dir)

    _run("Create calc.py with add(a, b)", base_cfg, work_dir, claude, codex, qwen)

    orch_dir = Path(work_dir) / ".orchestrator"
    for fname in ("memory.yaml", "bugs.md", "decisions.md", "key_facts.md", "issues.md"):
        assert (orch_dir / fname).exists(), f"{fname} missing after pipeline"


def test_memory_yaml_valid_dict_after_pipeline(
    work_dir: str, base_cfg: dict, patch_internal_agents
) -> None:
    """memory.yaml is a valid dict with all expected keys."""
    _PLAN = """\
## Shared Dependencies
None

## Files

### calc.py (CREATE)
- Implement add(a, b)
"""
    claude = StubClaudeAgent(["AGREED", "APPROVED"], work_dir)
    codex = StubCodexAgent(
        ["Tip.", "Tip.", "AGREED", _PLAN, "commit msg", "Updated."], work_dir
    )
    qwen = StubQwenAgent(QWEN_RESPONSES[:], work_dir)

    _run("Create calc.py", base_cfg, work_dir, claude, codex, qwen)

    mem = load_memory(work_dir)
    assert isinstance(mem, dict)
    for key in ("patterns_learned", "codebase_conventions", "past_mistakes",
                "architecture_decisions", "task_index"):
        assert key in mem, f"memory key '{key}' missing"


def test_implement_phase_only_writes_file(work_dir: str, base_cfg: dict) -> None:
    """phase='implement' runs only the implement step and creates the target file."""
    _PLAN = """\
## Shared Dependencies
None

## Files

### output.py (CREATE)
- Write a placeholder file
"""
    claude = StubClaudeAgent(
        ["AGREED", "APPROVED"],
        work_dir,
        write_filename="output.py",
    )
    codex = StubCodexAgent(
        ["Tip.", "Tip.", "AGREED", _PLAN, "commit", "Updated."],
        work_dir,
    )
    qwen = StubQwenAgent(QWEN_RESPONSES[:], work_dir)

    cfg = {**base_cfg, "discussion_rounds": 1}
    _run("Create output.py", cfg, work_dir, claude, codex, qwen, phase="implement")

    assert (Path(work_dir) / "output.py").exists()


def test_auto_approve_does_not_hang(
    work_dir: str, base_cfg: dict, patch_internal_agents
) -> None:
    """With auto-approve on, no gate should block the pipeline."""
    _PLAN = """\
## Shared Dependencies
None

## Files

### noop.py (CREATE)
- Write pass
"""
    claude = StubClaudeAgent(["AGREED", "APPROVED"], work_dir, write_filename="noop.py")
    codex = StubCodexAgent(
        ["Tip.", "Tip.", "AGREED", _PLAN, "add noop", "Updated."], work_dir
    )
    qwen = StubQwenAgent(QWEN_RESPONSES[:], work_dir)

    # If a gate blocks, this will hang and pytest will time out the test
    _run("Create noop.py", base_cfg, work_dir, claude, codex, qwen)
    assert True  # reached without hanging
