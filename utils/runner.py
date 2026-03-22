"""Subprocess runner with live streaming output, ESC-to-pause, and process tree management."""

from __future__ import annotations

import collections
import os
import subprocess
import threading
import time

import click
import msvcrt
import psutil
from rich.console import Console, Group
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


# ── CLI runner (piped I/O, live streaming output) ───────────────────


def run_cli(
    cmd: list[str],
    agent_name: str,
    input_text: str | None = None,
    timeout: int = 600,
    cwd: str | None = None,
    env: dict | None = None,
    no_timeout: bool = False,
    quiet: bool = False,
) -> tuple[str, str]:
    """Run a CLI subprocess with live streaming output and ESC-to-pause.

    Shows the last 20 lines of agent output as they arrive, plus a header
    with elapsed time.  When no output has arrived yet, falls back to a
    spinner.

    On ESC the process tree is *suspended* (frozen, not killed) and the
    user is prompted to **resume** or **kill**.  Only "kill" raises
    ``CancelledByUser``; "resume" continues seamlessly.

    Args:
        no_timeout: When True, the timeout dialog is never shown.
                    ESC-to-pause still works.  Use for long-running tasks
                    like Claude implementation where there is no expected
                    upper bound.
        quiet: When True, skip the Live spinner display and ESC watcher
               entirely.  Use when the caller already has its own Live
               context (e.g. the research phase table) to prevent nested
               Live displays from corrupting the terminal.

    Returns (stdout, stderr).
    Raises CancelledByUser  if the user chooses to kill.
    Raises TimeoutSkipToReview  if the user chooses "skip" on timeout.
    Raises TimeoutError  if the user chooses "kill" on timeout.
    """
    cancelled = threading.Event()
    process_done = threading.Event()
    original_timeout = timeout

    # Rolling window of output lines for the live display
    output_lines: collections.deque[str] = collections.deque(maxlen=20)
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    results: dict[str, str] = {"stdout": "", "stderr": ""}

    def _watch_esc() -> None:
        """Monitor for ESC key press in a background thread."""
        while not process_done.is_set():
            try:
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key == b'\x1b':  # ESC
                        cancelled.set()
                        return
            except Exception:
                pass
            time.sleep(0.1)

    start = time.time()

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=True,
        cwd=cwd,
        env=env,
    )

    # ── Background I/O threads ──────────────────────────────────────

    def _write_stdin() -> None:
        try:
            if input_text:
                proc.stdin.write(input_text)
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.stdin.close()
        except Exception:
            pass

    def _read_stdout() -> None:
        try:
            for line in proc.stdout:
                stripped = line.rstrip("\r\n")
                stdout_chunks.append(line)
                if stripped:
                    output_lines.append(stripped)
        except Exception:
            pass

    def _read_stderr() -> None:
        try:
            stderr_chunks.append(proc.stderr.read())
        except Exception:
            pass

    def _wait_all() -> None:
        stdout_t.join()
        stderr_t.join()
        proc.wait()
        results["stdout"] = "".join(stdout_chunks)
        results["stderr"] = "".join(stderr_chunks)
        process_done.set()

    threading.Thread(target=_write_stdin, daemon=True).start()
    stdout_t = threading.Thread(target=_read_stdout, daemon=True)
    stderr_t = threading.Thread(target=_read_stderr, daemon=True)
    stdout_t.start()
    stderr_t.start()
    threading.Thread(target=_wait_all, daemon=True).start()

    # ── Quiet mode: no Live display, no ESC watcher ─────────────────

    if quiet:
        process_done.wait()
        elapsed = time.time() - start
        console.print(f"  [dim]{agent_name} finished in {format_duration(elapsed)}[/]")
        return results["stdout"], results["stderr"]

    # ── Colour scheme ───────────────────────────────────────────────

    style = {
        "Claude": "cyan",
        "Codex": "green",
        "Qwen": "magenta",
    }.get(agent_name.split(" ")[0], "white")

    # ── Live display loop ───────────────────────────────────────────

    esc_thread = threading.Thread(target=_watch_esc, daemon=True)
    esc_thread.start()

    try:
        with Live(console=console, refresh_per_second=8, transient=True) as live:
            while not process_done.is_set():

                # ── ESC pressed: suspend and prompt ──────────────
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
                        type=click.Choice(["resume", "kill"], case_sensitive=False),
                        default="resume",
                    )

                    if choice == "resume":
                        console.print(f"[dim]Resuming {agent_name} ...[/]")
                        _resume_tree(proc)
                        cancelled.clear()
                        esc_thread = threading.Thread(target=_watch_esc, daemon=True)
                        esc_thread.start()
                        live.start()
                        continue
                    else:
                        _kill_tree(proc)
                        raise CancelledByUser(
                            f"{agent_name} operation killed by user (ESC)"
                        )

                elapsed = time.time() - start
                time_str = format_duration(elapsed)

                # ── Timeout dialog (skipped when no_timeout=True) ─
                if not no_timeout and elapsed > timeout:
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
                            ["continue", "skip", "kill"], case_sensitive=False
                        ),
                        default="continue",
                    )
                    if choice == "continue":
                        timeout += original_timeout
                        console.print(
                            f"[dim]Extended timeout — resuming {agent_name} ...[/]"
                        )
                        live.start()
                        continue
                    elif choice == "skip":
                        _kill_tree(proc)
                        raise TimeoutSkipToReview(
                            f"{agent_name} timed out — user chose to skip to review"
                        )
                    else:
                        _kill_tree(proc)
                        raise TimeoutError(
                            f"{agent_name} timed out after {timeout_str}"
                        )

                # ── Build live display ────────────────────────────
                header_text = Text.assemble(
                    (f" {agent_name}", f"bold {style}"),
                    " working... ",
                    (f"({time_str})", "dim"),
                    " — press ",
                    ("ESC", "bold yellow"),
                    " to pause",
                )
                header = Spinner("dots", text=header_text)

                lines = list(output_lines)
                if lines:
                    output_text = Text("\n".join(lines), style="dim", overflow="fold")
                    live.update(Group(header, output_text))
                else:
                    live.update(header)

                process_done.wait(timeout=0.125)

    finally:
        process_done.set()  # Stop ESC watcher

    elapsed = time.time() - start
    console.print(f"  [dim]{agent_name} finished in {format_duration(elapsed)}[/]")

    return results["stdout"], results["stderr"]


# ── Interactive runner (inherited stdio, no capture) ────────────────


def run_interactive(
    cmd: list[str],
    agent_name: str,
    timeout: int = 600,
    cwd: str | None = None,
    env: dict | None = None,
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
        env=env,
    )

    while True:
        try:
            exit_code = proc.wait(timeout=1)
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
                    ["continue", "skip", "kill"], case_sensitive=False
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
            else:
                _kill_tree(proc)
                raise TimeoutError(
                    f"{agent_name} timed out after {timeout_str}"
                )
