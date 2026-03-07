"""Subprocess runner with live spinner, ESC-to-pause, and process tree management."""

from __future__ import annotations

import os
import subprocess
import threading
import time

import click
import msvcrt
import psutil
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from utils.logger import format_duration

console = Console()


class CancelledByUser(Exception):
    """Raised when the user presses ESC and then chooses to kill."""


class TimeoutSkipToReview(Exception):
    """Raised when the user chooses to skip to review after a timeout."""


# ── Process tree helpers ────────────────────────────────────────────


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a subprocess and its entire child tree.

    On Windows with ``shell=True``, ``proc.terminate()`` only kills the
    ``cmd.exe`` wrapper, leaving the actual CLI process (claude, codex,
    qwen) alive.  This helper uses *psutil* to find every descendant and
    kill them all, then falls back to ``taskkill /T /F`` if needed.
    """
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.kill()
            parent.wait(timeout=5)
        except (psutil.NoSuchProcess, psutil.TimeoutExpired):
            pass
    except psutil.NoSuchProcess:
        pass
    except Exception:
        # Fallback: Windows taskkill with tree flag
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except Exception:
            try:
                os.kill(proc.pid, 9)
            except OSError:
                pass


def _suspend_tree(proc: subprocess.Popen) -> None:
    """Suspend (freeze) a subprocess and its entire child tree.

    When suspended the process consumes zero CPU and makes no API calls.
    """
    try:
        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            try:
                child.suspend()
            except psutil.NoSuchProcess:
                pass
        parent.suspend()
    except psutil.NoSuchProcess:
        pass


def _resume_tree(proc: subprocess.Popen) -> None:
    """Resume a previously suspended subprocess and its children."""
    try:
        parent = psutil.Process(proc.pid)
        parent.resume()
        for child in parent.children(recursive=True):
            try:
                child.resume()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass


# ── CLI runner (piped I/O, spinner) ─────────────────────────────────


def run_cli(
    cmd: list[str],
    agent_name: str,
    input_text: str | None = None,
    timeout: int = 600,
    cwd: str | None = None,
    env: dict | None = None,
) -> tuple[str, str]:
    """Run a CLI subprocess with a live spinner and ESC-to-pause.

    Shows: [spinner] Claude thinking... (1m 23s) — press ESC to pause

    On ESC the process tree is *suspended* (frozen, not killed) and the
    user is prompted to **resume** or **kill**.  Only "kill" raises
    ``CancelledByUser``; "resume" continues seamlessly.

    Returns (stdout, stderr).
    Raises CancelledByUser  if the user chooses to kill.
    Raises TimeoutSkipToReview  if the user chooses "skip" on timeout.
    Raises TimeoutError  if the user chooses "kill" on timeout.
    """
    cancelled = threading.Event()
    stdout_result = ""
    stderr_result = ""
    communicate_done = threading.Event()
    original_timeout = timeout

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
        env=env,
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
                # ── ESC pressed: suspend and prompt ──
                if cancelled.is_set():
                    _suspend_tree(proc)
                    live.stop()

                    console.print()
                    console.print(
                        f"[bold yellow]⏸  Paused:[/] {agent_name}  "
                        f"[dim](subprocess frozen — not consuming tokens)[/]"
                    )
                    console.print()

                    choice = click.prompt(
                        click.style("What now?", fg="yellow", bold=True),
                        type=click.Choice(
                            ["resume", "kill"],
                            case_sensitive=False,
                        ),
                        default="resume",
                    )

                    if choice == "resume":
                        console.print(f"[dim]Resuming {agent_name} ...[/]")
                        _resume_tree(proc)
                        # Reset for another ESC press
                        cancelled.clear()
                        esc_thread = threading.Thread(
                            target=_watch_esc, daemon=True
                        )
                        esc_thread.start()
                        live.start()
                        continue
                    else:  # kill
                        _kill_tree(proc)
                        raise CancelledByUser(
                            f"{agent_name} operation killed by user (ESC)"
                        )

                elapsed = time.time() - start
                time_str = format_duration(elapsed)

                spinner_text = Text.assemble(
                    (f" {agent_name}", f"bold {style}"),
                    " working... ",
                    (f"({time_str})", "dim"),
                    " — press ",
                    ("ESC", "bold yellow"),
                    " to pause",
                )
                live.update(Spinner("dots", text=spinner_text))

                # Check timeout — prompt user instead of crashing
                if elapsed > timeout:
                    live.stop()
                    timeout_str = format_duration(timeout)
                    console.print()
                    console.print(
                        f"[bold yellow]⏱  {agent_name} timed out "
                        f"after {timeout_str}.[/]"
                    )
                    choice = click.prompt(
                        click.style("What now?", fg="yellow", bold=True),
                        type=click.Choice(
                            ["continue", "skip", "kill"],
                            case_sensitive=False,
                        ),
                        default="continue",
                    )
                    if choice == "continue":
                        timeout += original_timeout
                        console.print(
                            f"[dim]Extended timeout — resuming "
                            f"{agent_name} ...[/]"
                        )
                        live.start()
                        continue
                    elif choice == "skip":
                        _kill_tree(proc)
                        raise TimeoutSkipToReview(
                            f"{agent_name} timed out — user chose to "
                            f"skip to review"
                        )
                    else:  # kill
                        _kill_tree(proc)
                        raise TimeoutError(
                            f"{agent_name} timed out after {timeout_str}"
                        )

                communicate_done.wait(timeout=0.25)

    finally:
        cancelled.set()  # Stop the ESC watcher

    elapsed = time.time() - start
    console.print(f"  [dim]{agent_name} finished in {format_duration(elapsed)}[/]")

    return stdout_result, stderr_result


# ── Interactive runner (inherited stdio, no capture) ────────────────


def run_interactive(
    cmd: list[str],
    agent_name: str,
    timeout: int = 600,
    cwd: str | None = None,
) -> int:
    """Run a subprocess with inherited stdio (full TUI takes over terminal).

    No spinner, no output capture — the user sees everything in real-time.
    Still respects timeout: prompts continue/skip/kill on expiry.

    Returns the process exit code.
    Raises CancelledByUser if killed by timeout choice.
    Raises TimeoutSkipToReview if user chooses skip.
    """
    original_timeout = timeout
    start = time.time()

    proc = subprocess.Popen(
        cmd,
        stdin=None,
        stdout=None,
        stderr=None,
        shell=True,
        cwd=cwd,
    )

    while True:
        try:
            # Poll with a short wait so we can check timeout
            exit_code = proc.wait(timeout=1)
            # Process finished
            elapsed = time.time() - start
            console.print(f"  [dim]{agent_name} finished in {format_duration(elapsed)}[/]")
            return exit_code
        except subprocess.TimeoutExpired:
            pass

        elapsed = time.time() - start
        if elapsed > timeout:
            timeout_str = format_duration(timeout)
            console.print()
            console.print(
                f"[bold yellow]⏱  {agent_name} timed out "
                f"after {timeout_str}.[/]"
            )
            choice = click.prompt(
                click.style("What now?", fg="yellow", bold=True),
                type=click.Choice(
                    ["continue", "skip", "kill"],
                    case_sensitive=False,
                ),
                default="continue",
            )
            if choice == "continue":
                timeout += original_timeout
                console.print(
                    f"[dim]Extended timeout — resuming {agent_name} ...[/]"
                )
                continue
            elif choice == "skip":
                _kill_tree(proc)
                raise TimeoutSkipToReview(
                    f"{agent_name} timed out — user chose to skip to review"
                )
            else:  # kill
                _kill_tree(proc)
                raise TimeoutError(
                    f"{agent_name} timed out after {timeout_str}"
                )
