"""Subprocess runner with live spinner, elapsed time, and ESC-to-cancel."""

from __future__ import annotations

import subprocess
import threading
import time

import msvcrt
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

console = Console()


class CancelledByUser(Exception):
    """Raised when the user presses ESC to cancel the current operation."""


def run_cli(
    cmd: list[str],
    agent_name: str,
    input_text: str | None = None,
    timeout: int = 600,
    cwd: str | None = None,
) -> tuple[str, str]:
    """Run a CLI subprocess with a live spinner and ESC-to-cancel.

    Shows: [spinner] Claude thinking... (1m 23s) — press ESC to cancel

    Returns (stdout, stderr).
    Raises CancelledByUser if the user presses ESC.
    Raises RuntimeError on non-zero exit code.
    """
    cancelled = threading.Event()
    stdout_result = ""
    stderr_result = ""
    communicate_done = threading.Event()

    def _watch_esc():
        """Monitor for ESC key press in a background thread."""
        while not cancelled.is_set():
            try:
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key == b'\x1b':  # ESC
                        cancelled.set()
                        return
            except Exception:
                pass
            time.sleep(0.1)

    # Start ESC watcher
    esc_thread = threading.Thread(target=_watch_esc, daemon=True)
    esc_thread.start()

    start = time.time()

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        shell=True,
        cwd=cwd,
    )

    # Use communicate() in a thread to avoid deadlocks from full pipe buffers
    def _communicate():
        nonlocal stdout_result, stderr_result
        try:
            stdout_result, stderr_result = proc.communicate(
                input=input_text
            )
        except Exception:
            pass
        communicate_done.set()

    comm_thread = threading.Thread(target=_communicate, daemon=True)
    comm_thread.start()

    style = {
        "Claude": "cyan",
        "Codex": "green",
        "Qwen": "magenta",
    }.get(agent_name.split(" ")[0], "white")

    try:
        with Live(console=console, refresh_per_second=4, transient=True) as live:
            while not communicate_done.is_set():
                if cancelled.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise CancelledByUser(
                        f"{agent_name} operation cancelled by user (ESC)"
                    )

                elapsed = time.time() - start
                mins, secs = divmod(int(elapsed), 60)
                time_str = f"{mins}m {secs:02d}s" if mins else f"{secs}s"

                spinner_text = Text.assemble(
                    (f" {agent_name}", f"bold {style}"),
                    " working... ",
                    (f"({time_str})", "dim"),
                    " — press ",
                    ("ESC", "bold yellow"),
                    " to cancel",
                )
                live.update(Spinner("dots", text=spinner_text))

                # Check timeout
                if elapsed > timeout:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise TimeoutError(
                        f"{agent_name} timed out after {timeout}s"
                    )

                communicate_done.wait(timeout=0.25)

    finally:
        cancelled.set()  # Stop the ESC watcher

    elapsed = time.time() - start
    mins, secs = divmod(int(elapsed), 60)
    time_str = f"{mins}m {secs:02d}s" if mins else f"{secs}s"
    console.print(f"  [dim]{agent_name} finished in {time_str}[/]")

    return stdout_result, stderr_result
