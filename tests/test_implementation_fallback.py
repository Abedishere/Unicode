from __future__ import annotations

import re
from pathlib import Path

from agents.base import BaseAgent
from phases.implement import _implement_file_by_file
from utils.plan_parser import FileSpec, StructuredPlan
from utils.runner import UsageLimitReached


def _path_from_prompt(prompt: str) -> str:
    match = re.search(r"^ACTION:\s+\w+\s+(.+)$", prompt, re.MULTILINE)
    assert match is not None
    return match.group(1).strip()


class _WritingAgent(BaseAgent):
    def __init__(self, name: str, work_dir: str, *, limit_after: int | None = None) -> None:
        super().__init__(model="stub", timeout=30, working_dir=work_dir)
        self._name = name
        self._limit_after = limit_after
        self.calls = 0
        self.written: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    def query(self, prompt: str) -> str:
        return self.implement(prompt)

    def implement(self, prompt: str) -> str:
        if self._limit_after is not None and self.calls >= self._limit_after:
            raise UsageLimitReached(self.name, "simulated limit")
        self.calls += 1
        path = _path_from_prompt(prompt)
        target = Path(self.working_dir) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# written by {self.name}\n", encoding="utf-8")
        self.written.append(path)
        return f"Implemented {path}"


class _QueryOnlyFallback(BaseAgent):
    def __init__(self, name: str, work_dir: str) -> None:
        super().__init__(model="stub", timeout=30, working_dir=work_dir)
        self._name = name
        self.written: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    def query(self, prompt: str) -> str:
        path = _path_from_prompt(prompt)
        target = Path(self.working_dir) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# written by {self.name}\n", encoding="utf-8")
        self.written.append(path)
        return "query fallback wrote file"


def _plan() -> StructuredPlan:
    return StructuredPlan(
        shared_dependencies="None",
        files=[
            FileSpec("a.py", "CREATE", "- create a.py"),
            FileSpec("b.py", "CREATE", "- create b.py"),
            FileSpec("c.py", "CREATE", "- create c.py"),
        ],
    )


def test_limit_handoff_finishes_limited_and_pending_files(work_dir: str) -> None:
    claude = _WritingAgent("Claude", work_dir, limit_after=1)
    codex = _QueryOnlyFallback("Codex", work_dir)

    result = _implement_file_by_file(
        "create three files",
        _plan(),
        claude,  # type: ignore[arg-type]
        work_dir=work_dir,
        max_workers=1,
        fallback_agents=[codex],
    )

    assert (Path(work_dir) / "a.py").read_text(encoding="utf-8") == "# written by Claude\n"
    assert (Path(work_dir) / "b.py").read_text(encoding="utf-8") == "# written by Codex\n"
    assert (Path(work_dir) / "c.py").read_text(encoding="utf-8") == "# written by Codex\n"
    assert "[a.py] done" in result
    assert "[b.py] done" in result
    assert "[c.py] done" in result


def test_fallback_chain_continues_to_next_backup(work_dir: str) -> None:
    claude = _WritingAgent("Claude", work_dir, limit_after=0)
    codex = _WritingAgent("Codex", work_dir, limit_after=0)
    kiro = _WritingAgent("Kiro", work_dir)

    result = _implement_file_by_file(
        "create three files",
        _plan(),
        claude,  # type: ignore[arg-type]
        work_dir=work_dir,
        max_workers=1,
        fallback_agents=[codex, kiro],
    )

    for filename in ("a.py", "b.py", "c.py"):
        assert (Path(work_dir) / filename).read_text(encoding="utf-8") == "# written by Kiro\n"
        assert f"[{filename}] done" in result
