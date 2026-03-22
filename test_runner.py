"""Quick tests for the runner streaming and no_timeout features."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from utils.runner import run_cli

# Write helper scripts to current dir (no spaces, no quoting issues on Windows)
_SCRIPT_DIR = Path(__file__).parent

def _write_script(name: str, code: str) -> list[str]:
    """Write a .py helper script and return a run_cli command."""
    path = _SCRIPT_DIR / name
    path.write_text(code, encoding="utf-8")
    return ["python", name]


def test_streaming_output():
    """Lines appear in live display as they stream; all are captured in stdout."""
    print("\n=== TEST 1: Streaming output ===")
    print("5 lines printed with 0.3s gaps. Watch the live display below:\n")

    _write_script(
        "_t_stream.py",
        "import time\n"
        "for i in range(1, 6):\n"
        "    print(f'Line {i}: step {i} of 5', flush=True)\n"
        "    time.sleep(0.3)\n",
    )

    stdout, stderr = run_cli(["python", "_t_stream.py"], agent_name="Claude", timeout=30)
    lines = [l for l in stdout.strip().splitlines() if l]
    assert len(lines) == 5, f"Expected 5 lines, got {len(lines)}: {lines!r}  stderr={stderr!r}"
    assert lines[0].startswith("Line 1"), f"Bad first line: {lines[0]!r}"
    print(f"\nPASS: captured {len(lines)} lines correctly")


def test_no_timeout():
    """no_timeout=True lets a slow command run past timeout= without a dialog."""
    print("\n=== TEST 2: no_timeout flag ===")
    print("3s command with timeout=1 and no_timeout=True — no dialog should appear.\n")

    _write_script(
        "_t_slow.py",
        "import time\n"
        "time.sleep(3)\n"
        "print('completed', flush=True)\n",
    )

    start = time.time()
    stdout, _ = run_cli(["python", "_t_slow.py"], agent_name="Claude", timeout=1, no_timeout=True)
    elapsed = time.time() - start

    assert "completed" in stdout, f"Expected 'completed' in stdout, got: {stdout!r}"
    assert elapsed >= 3, f"Expected >= 3s elapsed, got {elapsed:.1f}s"
    print(f"\nPASS: ran {elapsed:.1f}s without timeout dialog")


def test_large_output():
    """All 50 lines captured in stdout; live display scrolls last 20."""
    print("\n=== TEST 3: Large output (50 lines) ===\n")

    _write_script(
        "_t_large.py",
        "for i in range(1, 51):\n"
        "    print(f'line {i}', flush=True)\n",
    )

    stdout, _ = run_cli(["python", "_t_large.py"], agent_name="Codex", timeout=30)
    lines = [l for l in stdout.strip().splitlines() if l]
    assert len(lines) == 50, f"Expected 50 lines, got {len(lines)}"
    assert lines[-1] == "line 50", f"Last line should be 'line 50', got: {lines[-1]!r}"
    print(f"\nPASS: all {len(lines)} lines captured, live display showed last 20")


def test_stdin_passthrough():
    """Input text is correctly piped to stdin of the subprocess."""
    print("\n=== TEST 4: stdin passthrough ===\n")

    _write_script(
        "_t_stdin.py",
        "import sys\n"
        "data = sys.stdin.read().strip()\n"
        "print(f'got: {data}', flush=True)\n",
    )

    stdout, _ = run_cli(
        ["python", "_t_stdin.py"],
        agent_name="Qwen",
        timeout=30,
        input_text="hello from orchestrator",
    )
    assert "hello from orchestrator" in stdout, f"stdin not echoed: {stdout!r}"
    print(f"\nPASS: stdin passthrough works: {stdout.strip()!r}")


def _cleanup():
    for name in ["_t_stream.py", "_t_slow.py", "_t_large.py", "_t_stdin.py"]:
        try:
            (_SCRIPT_DIR / name).unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    tests = [
        test_streaming_output,
        test_no_timeout,
        test_large_output,
        test_stdin_passthrough,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"\nFAIL: {e}")
            failed += 1
        except KeyboardInterrupt:
            print("\nInterrupted by user")
            break

    _cleanup()
    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
