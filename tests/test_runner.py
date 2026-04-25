"""Tests for utils/runner.run_cli() — subprocess streaming, timeouts, stdin."""
from __future__ import annotations

import sys
import time
from pathlib import Path


from utils.runner import run_cli


def _script(tmp_path: Path, name: str, code: str) -> list[str]:
    """Write a helper .py script to tmp_path and return the run_cli command."""
    p = tmp_path / name
    p.write_text(code, encoding="utf-8")
    return [sys.executable, str(p)]


def test_streaming_output(tmp_path: Path) -> None:
    """All 5 lines are captured in stdout."""
    cmd = _script(
        tmp_path,
        "stream.py",
        "import time\n"
        "for i in range(1, 6):\n"
        "    print(f'Line {i}', flush=True)\n"
        "    time.sleep(0.05)\n",
    )
    stdout, _ = run_cli(cmd, agent_name="Claude", timeout=30, quiet=True)
    lines = [l for l in stdout.strip().splitlines() if l]
    assert len(lines) == 5
    assert lines[0].startswith("Line 1")
    assert lines[-1].startswith("Line 5")


def test_no_timeout_flag(tmp_path: Path) -> None:
    """no_timeout=True lets a process run past timeout= without blocking."""
    cmd = _script(
        tmp_path,
        "slow.py",
        "import time\ntime.sleep(2)\nprint('done', flush=True)\n",
    )
    start = time.time()
    stdout, _ = run_cli(cmd, agent_name="Claude", timeout=1, no_timeout=True, quiet=True)
    elapsed = time.time() - start
    assert "done" in stdout
    assert elapsed >= 2


def test_large_output(tmp_path: Path) -> None:
    """All 50 lines are captured when output exceeds the live-display window."""
    cmd = _script(
        tmp_path,
        "large.py",
        "for i in range(1, 51):\n    print(f'line {i}', flush=True)\n",
    )
    stdout, _ = run_cli(cmd, agent_name="Codex", timeout=30, quiet=True)
    lines = [l for l in stdout.strip().splitlines() if l]
    assert len(lines) == 50
    assert lines[-1] == "line 50"


def test_stdin_passthrough(tmp_path: Path) -> None:
    """input_text is forwarded to the subprocess via stdin."""
    cmd = _script(
        tmp_path,
        "stdin.py",
        "import sys\ndata = sys.stdin.read().strip()\nprint(f'got: {data}', flush=True)\n",
    )
    stdout, _ = run_cli(
        cmd,
        agent_name="Kiro",
        timeout=30,
        input_text="hello orchestrator",
        quiet=True,
    )
    assert "hello orchestrator" in stdout
