"""AI Agent Orchestrator — coordinate Claude Code and Codex CLI."""

from __future__ import annotations

import concurrent.futures
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import click
import yaml
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agents.claude_agent import ClaudeAgent
from agents.codex_agent import CodexAgent, read_codex_config
from agents.qwen_agent import QwenAgent
from phases.discuss import run_discussion
from phases.implement import run_implementation
from phases.plan import consolidate_plan
from phases.research import run_research
from phases.review import run_review
from utils.approval import request_approval, reset_session_approvals, set_auto_all, is_auto_all
from utils.git_utils import commit, push, init_repo, is_git_repo
from utils.history import append_history, agent_update_md, init_agent_md, write_orchestrator_md
from utils.logger import format_duration, init_transcript, log_error, log_info, log_memory_context, log_phase, log_phase_outcome, log_success
from utils.memory import (
    extract_keywords_from_task,
    get_context_for_task, init_project_notes, load_memory,
    log_bug, log_decision, log_issue, log_key_fact, parse_json_response, save_memory,
)
from utils.init_project import run_init
from utils.runner import CancelledByUser, TimeoutSkipToReview
from utils.session import Session, save_session, load_session, list_sessions

try:
    from utils.repo_map import generate_repo_map
except ImportError:
    def generate_repo_map(working_dir, max_tokens=2000):
        return ""

try:
    from utils.plan_parser import parse_plan, is_structured
except ImportError:
    def parse_plan(text):
        return None
    def is_structured(plan):
        return False


PACKAGE_DIR = Path(__file__).resolve().parent

console = Console()

VERSION = "0.2.0"

_MAX_SESSIONS_DISPLAYED = 15

# 3-color gradient stops: orange (Claude) → teal (Codex) → purple (Qwen)
_C = "#E8915C"  # Claude orange/amber
_X = "#50C8B4"  # Codex teal/blue-green
_Q = "#7B68EE"  # Qwen purple/blue

# Box-drawing block letters (Qwen Code style)
_art_lines = [
    "  ██╗          ▄▄    ▄▄ ▄▄▄    ▄▄ ▄▄  ▄▄▄▄▄▄   ▄▄▄▄▄▄  ▄▄▄▄▄▄  ▄▄▄▄▄▄▄ ",
    "    ██╗        ██║   ██║████╗  ██║██║██╔════╝ ██╔═══██╗██╔══██╗██╔════╝",
    "      ██╗      ██║   ██║██╔██╗ ██║██║██║      ██║   ██║██║  ██║█████╗  ",
    "        ██╗    ██║   ██║██║╚██╗██║██║██║      ██║   ██║██║  ██║██╔══╝  ",
    "        ██╝    ╚██████╔╝██║ ╚████║██║╚██████╗ ╚██████╔╝██████╔╝███████╗",
    "      ██╝        ╚═════╝ ╚═╝  ╚═══╝╚═╝ ╚═════╝  ╚═════╝ ╚═════╝ ╚══════╝",
    "    ██╝",
    "  ██╝",
]


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> str:
    r = int(c1[0] + (c2[0] - c1[0]) * t)
    g = int(c1[1] + (c2[1] - c1[1]) * t)
    b = int(c1[2] + (c2[2] - c1[2]) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


_GRADIENT_STOPS = (_hex_to_rgb(_C), _hex_to_rgb(_X), _hex_to_rgb(_Q))


def _gradient_char(col: int, width: int) -> str:
    """Return a hex color for column position using a 3-stop gradient."""
    t = col / max(width - 1, 1)  # 0.0 → 1.0
    if t <= 0.5:
        return _lerp_color(_GRADIENT_STOPS[0], _GRADIENT_STOPS[1], t / 0.5)
    else:
        return _lerp_color(_GRADIENT_STOPS[1], _GRADIENT_STOPS[2], (t - 0.5) / 0.5)


def _gradient_line(line: str, width: int) -> str:
    """Apply per-character gradient to a line of art."""
    rich_line = ""
    for i, ch in enumerate(line):
        if ch == " ":
            rich_line += " "
        else:
            color = _gradient_char(i, width)
            rich_line += f"[bold {color}]{ch}[/]"
    return rich_line


def _build_gradient_art() -> str:
    """Build ASCII art with gradient coloring."""
    max_width = max(len(l) for l in _art_lines)
    return "\n".join(_gradient_line(line, max_width) for line in _art_lines)


_ASCII_ART_CACHE: str | None = None


def _get_ascii_art() -> str:
    """Lazily build and cache the gradient ASCII art."""
    global _ASCII_ART_CACHE
    if _ASCII_ART_CACHE is None:
        _ASCII_ART_CACHE = _build_gradient_art()
    return _ASCII_ART_CACHE


def _print_banner(cfg: dict, work_dir: str) -> None:
    """Print the startup banner with ASCII art left and info box right."""
    console.print()

    dev_model = cfg.get("dev_model", cfg["claude_model"])

    # Info panel (right side)
    info_lines = Text()
    info_lines.append(">_ ", style="bold magenta")
    info_lines.append(f"Unicode Orchestrator", style="bold white")
    info_lines.append(f" (v{VERSION})\n\n", style="dim")
    info_lines.append(f"  Claude ", style=f"bold {_C}")
    info_lines.append(f"{cfg['claude_model']}", style="dim")
    info_lines.append(f"  |  ", style="dim")
    codex_display = cfg["codex_model"]
    if not codex_display:
        codex_cfg = read_codex_config()
        codex_display = codex_cfg.get("model", "codex-default")
        effort = codex_cfg.get("reasoning_effort")
        if effort:
            codex_display += f" ({effort})"
    info_lines.append(f"Codex ", style=f"bold {_X}")
    info_lines.append(f"{codex_display}", style="dim")
    info_lines.append(f"\n  Qwen ", style=f"bold {_Q}")
    info_lines.append(f"{cfg['qwen_model']}", style="dim")
    info_lines.append(f"  |  ", style="dim")
    info_lines.append(f"Dev ", style=f"bold {_C}")
    info_lines.append(f"{dev_model}", style="dim")
    info_lines.append(f"\n  {work_dir}", style="dim")

    info_panel = Panel(
        info_lines,
        border_style="bright_black",
        padding=(0, 1),
    )

    console.print(
        Columns([_get_ascii_art(), info_panel], padding=(0, 2), expand=False),
    )

    console.print()
    console.print(
        "[dim]Tips: Type your task or pass it as an argument. "
        "Drag/paste image paths or use /image <path> to attach images. "
        "Press Ctrl+C twice to exit.[/]"
    )
    console.print()

# Tracks when the last Ctrl+C was pressed for double-press detection
_last_ctrl_c: float = 0.0
_DOUBLE_PRESS_WINDOW = 2.0  # seconds

# Current session — saved on hard exit so the user can resume later.
_current_session: Session | None = None
_current_work_dir: str | None = None


def _sigint_handler(signum, frame):
    """Handle Ctrl+C: first press does nothing, second press within 2s exits.

    On double-press, saves the current session (if any) so it can be resumed.
    """
    global _last_ctrl_c
    now = time.time()
    if now - _last_ctrl_c <= _DOUBLE_PRESS_WINDOW:
        # Save session before hard exit
        if _current_session and _current_work_dir:
            _current_session.status = "paused"
            save_session(_current_work_dir, _current_session)
            console.print(
                f"\n[bold yellow]Session [cyan]{_current_session.session_id}[/cyan] "
                f"saved.  Resume with:[/]  unicode --resume {_current_session.session_id}"
            )
        console.print("[bold red]Ctrl+C ×2 — exiting unicode.[/]")
        os._exit(0)
    _last_ctrl_c = now
    console.print("\n[bold yellow]Press Ctrl+C again within 2s to exit.[/]")


# ── Tier definitions ─────────────────────────────────────────────────
_DEFAULT_TIERS = {
    "quick": {
        "dev_model": "sonnet",
        "max_review_iterations": 1,
        "discussion_rounds": 1,
    },
    "standard": {
        "dev_model": "sonnet",
        "max_review_iterations": 2,
        "discussion_rounds": 2,
    },
    "complex": {
        "dev_model": "opus",
        "max_review_iterations": 3,
        "discussion_rounds": 4,
    },
}


def _prompt_tier(cfg: dict) -> str:
    """Interactively ask the user to select a complexity tier.

    Returns the tier name. Modifies cfg in-place with the tier's settings.
    """
    tiers = cfg.get("tiers", _DEFAULT_TIERS)

    console.print()
    console.print(Panel(
        "[bold]Select task complexity tier[/]",
        title="[bold cyan]Tier Selection[/]",
        border_style="cyan",
    ))

    tier_info = {
        "quick": ("Quick fix / simple task", "Sonnet dev, 1 review"),
        "standard": ("Standard task", "Sonnet dev, 2 reviews"),
        "complex": ("Complex / architectural", "Opus dev, 3 reviews"),
    }

    for key in ("quick", "standard", "complex"):
        label, detail = tier_info.get(key, (key, ""))
        dev = tiers.get(key, {}).get("dev_model", "sonnet")
        console.print(f"  [bold magenta]{key[0]}[/] — {label} [dim]({detail}, dev:{dev})[/]")

    console.print()
    choice = click.prompt(
        click.style("Tier", fg="cyan", bold=True),
        type=click.Choice(["q", "s", "c"], case_sensitive=False),
        default="s",
        show_choices=False,
    )

    tier_map = {"q": "quick", "s": "standard", "c": "complex"}
    tier_name = tier_map[choice]
    tier_cfg = tiers.get(tier_name, {})

    # Apply tier settings to cfg
    for key, val in tier_cfg.items():
        cfg[key] = val

    console.print(f"[dim]  Tier: {tier_name} (dev:{cfg.get('dev_model', 'sonnet')})[/]")
    return tier_name


def _prompt_auto_mode() -> bool:
    """Ask if the user wants auto-approve-all mode for this task."""
    console.print()
    console.print(
        "  [bold magenta]a[/] — Auto mode [dim](skip all approvals except git commit)[/]"
    )
    console.print(
        "  [bold magenta]m[/] — Manual mode [dim](approve each phase individually)[/]"
    )
    console.print()
    choice = click.prompt(
        click.style("Mode", fg="cyan", bold=True),
        type=click.Choice(["a", "m"], case_sensitive=False),
        default="m",
        show_choices=False,
    )
    return choice == "a"


def load_config(config_path: str | None) -> dict:
    defaults = {
        "discussion_rounds": 4,
        "max_review_iterations": 3,
        "claude_model": "opus",
        "codex_model": None,  # None → use ~/.codex/config.toml
        "qwen_model": "qwen3-coder",
        "dev_model": "sonnet",
        "timeout_seconds": 600,
        "codex_timeout_seconds": 300,
        "auto_commit": False,
        "allow_user_questions": True,
        "working_directory": ".",
        "tiers": _DEFAULT_TIERS,
    }
    # Resolve config: explicit path → CWD → package dir
    resolved = None
    if config_path:
        candidate = Path(config_path)
        if candidate.exists():
            resolved = candidate
        elif (PACKAGE_DIR / candidate.name).exists():
            resolved = PACKAGE_DIR / candidate.name
    if resolved:
        with open(resolved, "r", encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        defaults.update(overrides)
    return defaults


def _print_phase_banner(label: str, role: str, desc: str, color: str = "cyan") -> None:
    """Print a colored phase banner panel (like Qwen Code's notice boxes)."""
    content = Text()
    content.append(f"Talking to ", style="default")
    content.append(role, style=f"bold {color}")
    content.append(f" — {desc}", style="dim")
    console.print()
    console.print(Panel(
        content,
        title=f"[bold {color}]{label}[/]",
        border_style=color,
        padding=(0, 1),
    ))
    console.print()


def _show_sessions() -> None:
    """Print a summary table of saved sessions."""
    if _current_work_dir is None:
        console.print("[dim]  No working directory set yet.[/]")
        return
    sessions = list_sessions(_current_work_dir)
    if not sessions:
        console.print("[dim]  No saved sessions.[/]")
        return
    console.print()
    for s in sessions[:_MAX_SESSIONS_DISPLAYED]:
        color = {
            "running": "yellow", "paused": "yellow",
            "completed": "green", "failed": "red",
        }.get(s.status, "dim")
        phase = s.current_phase or s.next_incomplete_phase() or "done"
        task_preview = s.task[:55].replace("\n", " ")
        console.print(
            f"  [bold cyan]{s.session_id}[/]  "
            f"[{color}]{s.status:<10}[/] "
            f"[dim]phase: {phase:<12}[/] "
            f"[dim]{task_preview}[/]"
        )
    console.print()


def _run_phase(label: str, fn, *args, **kwargs):
    """Run a phase function, catching ESC cancellation gracefully.

    On ESC → kill: asks retry / skip / clarify.
    Returns the function result, or None if skipped.
    """
    while True:
        try:
            return fn(*args, **kwargs)
        except CancelledByUser:
            console.print()
            console.print(f"[bold yellow]Stopped:[/] {label}")
            console.print()
            choice = click.prompt(
                click.style("What now?", fg="yellow", bold=True),
                type=click.Choice(["retry", "clarify", "skip"], case_sensitive=False),
                default="retry",
            )
            if choice == "retry":
                console.print(f"[dim]Retrying {label} ...[/]")
                continue
            elif choice == "clarify":
                clarification = click.prompt(
                    click.style("Your instructions", fg="cyan", bold=True),
                    default="",
                    show_default=False,
                )
                if clarification.strip():
                    # Prepend clarification to the first string argument
                    new_args = list(args)
                    for i, a in enumerate(new_args):
                        if isinstance(a, str):
                            new_args[i] = f"{a}\n\nUSER CLARIFICATION:\n{clarification.strip()}"
                            break
                    args = tuple(new_args)
                console.print(f"[dim]Retrying {label} with your instructions ...[/]")
                continue
            else:
                log_info(f"Skipping {label}.")
                return None


def _prompt_gradient_line() -> str:
    """Build a gradient underline: orange → teal → purple."""
    w = console.width
    third = w // 3
    return (
        f"[{_C}]{'━' * third}[/]"
        f"[{_X}]{'━' * third}[/]"
        f"[{_Q}]{'━' * (w - 2 * third)}[/]"
    )


# ── Image attachment and paste detection state ──
_IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tiff", ".ico",
})
_paste_counter: int = 0
_image_counter: int = 0
_attached_images: list[tuple[int, str]] = []  # (image_number, absolute_path)
_attached_pastes: list[tuple[int, list[str]]] = []  # (paste_number, lines)
# Badge count when the prompt area was last drawn.  Used by _erase_screen_from
# and _redraw_prompt_area to move the cursor back with relative movement
# (scroll-safe) instead of the DEC save/restore that breaks near screen bottom.
_prompt_draw_badge_count: int = 0


def _clean_path(text: str) -> str:
    """Normalize a pasted/dragged file path for Windows and Unix.

    Aggressively strips quotes, whitespace, control characters, and common
    terminal drag-and-drop artefacts.
    """
    s = text.strip()
    # Remove control chars (can sneak in from paste/drag)
    s = "".join(ch for ch in s if ord(ch) >= 32 or ch in ("\t",))
    # Windows Terminal PowerShell drag: & 'path' or & "path"
    if s.startswith("& "):
        s = s[2:].strip()
    # Strip surrounding quotes — try matched pairs first, then lone quotes
    for q in ('"', "'", '`'):
        if s.startswith(q) and s.endswith(q) and len(s) > 1:
            s = s[1:-1]
            break
    else:
        # No matched pair — strip any leading/trailing quote individually
        s = s.strip('"').strip("'").strip('`')
    # file:/// URI
    if s.lower().startswith("file:///"):
        s = s[8:]
    return s.strip()


def _is_image_path(text: str) -> str | None:
    """Heuristic: does *text* look like a path to an image file?

    Checks two things:
    1. Looks like a file path (drive letter, path separators, etc.)
    2. Ends with a known image extension.

    Does **not** require the file to exist — drag-and-drop paths from
    other machines, network drives, or recently moved files still match.

    Returns the cleaned path string if it looks like an image, else None.
    """
    s = _clean_path(text)
    if not s:
        return None

    # Must have an image extension
    p = Path(s)
    if p.suffix.lower() not in _IMAGE_EXTENSIONS:
        return None

    # Must look like a real path (not just "foo.jpg" typed as text)
    is_path = (
        "/" in s
        or "\\" in s
        or (len(s) > 2 and s[1] == ":")      # drive letter  C:\...
        or s.startswith("~")                   # home dir
    )
    if not is_path:
        return None

    return s


def _humanize_size(nbytes: int) -> str:
    """Return a human-readable file size string."""
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.0f} {unit}" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def _image_badge(num: int, path: str) -> str:
    """Return a Claude Code-style image badge string for Rich."""
    name = Path(path).name
    try:
        size = _humanize_size(Path(path).stat().st_size)
    except OSError:
        size = ""
    size_str = f" ({size})" if size else ""
    return f"[bold cyan on grey23] \\[Image #{num}] [/] [dim]{name}{size_str}[/]"


def _try_attach_image(text: str) -> bool:
    """If *text* looks like a path to an image, attach it and return True."""
    global _image_counter
    img_path = _is_image_path(text)
    if img_path is None:
        return False
    # Try to resolve to an absolute path; if the file exists use the
    # resolved path, otherwise keep the original cleaned path as-is.
    p = Path(img_path).expanduser()
    try:
        final = str(p.resolve()) if p.exists() else img_path
    except OSError:
        final = img_path
    _image_counter += 1
    _attached_images.append((_image_counter, final))
    return True


def _paste_is_image_path(paste: list[str]) -> bool:
    """Check if a multi-line paste payload is really just a drag-dropped image path.

    Drag-and-drop on Windows Terminal often produces a multi-line paste with
    trailing blank lines or PowerShell artefacts like ``& 'C:\\path\\img.png'``.
    Collapse non-blank lines into one string and test with _try_attach_image.
    """
    collapsed = " ".join(line for line in paste if line.strip())
    return _try_attach_image(collapsed)


def _handle_image_command(cmd: str) -> None:
    """Process ``/image <path>`` — validate and attach an image."""
    global _image_counter
    parts = cmd.split(maxsplit=1)
    if len(parts) < 2:
        console.print("[dim]Usage: /image <path>[/]")
        return
    raw = _clean_path(parts[1])
    if not raw:
        console.print("[dim red]  Empty path.[/]")
        return
    p = Path(raw).expanduser()
    if p.suffix.lower() not in _IMAGE_EXTENSIONS:
        console.print(
            f"[dim red]  Unsupported format: {p.suffix}  "
            f"(supported: {', '.join(sorted(_IMAGE_EXTENSIONS))})[/]"
        )
        return
    try:
        final = str(p.resolve()) if p.exists() else raw
    except OSError:
        final = raw
    _image_counter += 1
    _attached_images.append((_image_counter, final))
    console.print(f"  {_image_badge(_image_counter, final)}")


def _flush_stdin():
    """Discard leftover data in stdin buffer (prevents paste leaking)."""
    if os.name == 'nt':
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getwch()
    else:
        import select
        while select.select([sys.stdin], [], [], 0)[0]:
            sys.stdin.readline()


def _has_stdin_data() -> bool:
    """Return True if stdin has buffered data ready to read (non-blocking)."""
    if os.name == "nt":
        import msvcrt
        return msvcrt.kbhit()
    else:
        import select
        return bool(select.select([sys.stdin], [], [], 0)[0])


def _drain_stdin_lines() -> list[str]:
    """Read all lines currently buffered in stdin without blocking.

    Uses low-level OS calls so nothing extra is printed to screen.
    Returns the collected lines (newlines stripped).
    """
    buffered: list[str] = []
    if os.name == "nt":
        import msvcrt
        current: list[str] = []
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                buffered.append("".join(current))
                current = []
            else:
                current.append(ch)
        if current:
            buffered.append("".join(current))
    else:
        import select
        while select.select([sys.stdin], [], [], 0)[0]:
            raw = sys.stdin.readline()
            if not raw:
                break
            buffered.append(raw.rstrip("\n\r"))
    return buffered


def _erase_screen_from(saved: bool = True) -> None:
    """Erase all terminal content from before the prompt area.

    Uses relative cursor movement (scroll-safe) instead of DEC save/restore.
    Must be called after _prompt_line_raw has returned — cursor is on the line
    after the prompt (due to the \\r\\n written before returning).

    Layout when called (N = _prompt_draw_badge_count):
        ...                 ← want cursor here, then erase ↓
        ── rule ──          (1 line)
        badge 1 … badge N  (N lines)
        > <text>           (1 line, prompt)
        <cursor here>      (new line from \\r\\n)

    Lines to move up: N + 3  (rule + badges + prompt + \\r\\n line)
    """
    n_up = _prompt_draw_badge_count + 3
    sys.stdout.write(f"\033[{n_up}A\033[J")
    sys.stdout.flush()


# ── Slash command definitions ──────────────────────────────────────────
_SLASH_COMMANDS = [
    ("/image <path>", "Attach an image file"),
    ("/clear", "Remove all attachments"),
    ("/clear-images", "Remove attached images only"),
    ("/clear-paste", "Remove pasted text only"),
    ("/auto", "Toggle auto-approve mode"),
    ("/ask <question>", "Ask the admin agents a question (no task started)"),
    ("/init", "Scan project & bootstrap all memory files from existing code"),
    ("/sessions", "Browse & resume a saved session"),
    ("/resume <id>", "Resume a saved session by ID"),
    ("/pause", "Save & pause current session"),
    ("/stop", "Stop orchestrator (no save)"),
]


def _clear_below_cursor(n: int, restore_col: int = 0) -> None:
    """Erase everything below the prompt line and return the cursor.

    Uses ``\\r\\n`` to reach column 1 of the next line, then ``\\033[J``
    (erase-from-cursor-to-end-of-display) to nuke all content below.
    ``\\033[A`` returns to the prompt line; *restore_col* repositions
    horizontally.  *n* only needs to be > 0 to trigger the erase.
    """
    if n <= 0:
        return
    sys.stdout.write("\r\n\033[J")  # col 1 of next line, erase to end
    sys.stdout.write("\033[A")      # back up to prompt line
    if restore_col > 0:
        sys.stdout.write(f"\033[{restore_col}G")
    sys.stdout.flush()


def _render_slash_menu(typed: str, prev_n: int, restore_col: int = 0, sel: int = -1) -> int:
    """Show the slash-command picker below the prompt line.

    *sel* is the 0-based index of the highlighted row (-1 = none).

    Returns a positive count if a menu was drawn (for *prev_n* tracking),
    0 if nothing matched.  Always erases below the cursor first via
    ``\\033[J``, so stale lines can never accumulate.
    """
    # Match against the command keyword only (before any space/arg)
    base = typed.split()[0] if typed.split() else typed
    matches = [(c, d) for c, d in _SLASH_COMMANDS if c.split()[0].startswith(base)]

    # ── Nuke everything below the prompt line ────────────────────
    sys.stdout.write("\r\n\033[J\033[A")     # down, erase-to-end, back up

    if not matches:
        if restore_col > 0:
            sys.stdout.write(f"\033[{restore_col}G")
        sys.stdout.flush()
        return 0

    # ── Draw menu: blank gap + match lines ───────────────────────
    # We'll count newlines so we know exactly how many rows to go back.
    rows_down = 0
    sys.stdout.write("\r\n")                 # blank gap line
    rows_down += 1
    for i, (cmd, desc) in enumerate(matches):
        if i == sel:
            sys.stdout.write(f"\r\n \033[1;35m>\033[0m \033[1;35;7m{cmd:<22}\033[0m \033[2m{desc}\033[0m")
        else:
            sys.stdout.write(f"\r\n   \033[1;35m{cmd:<22}\033[0m \033[2m{desc}\033[0m")
        rows_down += 1

    # ── Return to prompt line ────────────────────────────────────
    sys.stdout.write(f"\033[{rows_down}A")
    if restore_col > 0:
        sys.stdout.write(f"\033[{restore_col}G")
    sys.stdout.flush()
    return rows_down                          # > 0 signals "menu visible"


# ── Session picker (same visual style as attachment selection) ─────────

def _run_session_picker() -> str | None:
    """Interactive session picker rendered below the current cursor position.

    ↑/↓ navigate sessions, Enter to resume selected, Esc/Backspace to cancel.

    Uses **relative** cursor movement (``\\033[nA\\033[J``) rather than DEC
    save/restore so the menu redraws correctly even when the terminal has
    scrolled since the picker was first opened.

    Returns the ``session_id`` of the chosen session, or ``None`` if cancelled.
    """
    import msvcrt

    if _current_work_dir is None:
        console.print("[dim]  No working directory set yet.[/]")
        return None

    sessions = list_sessions(_current_work_dir)
    if not sessions:
        console.print("[dim]  No saved sessions.[/]")
        return None

    sessions = sessions[:_MAX_SESSIONS_DISPLAYED]
    total = len(sessions)
    sel = 0
    menu_height = 0   # lines drawn by the last _draw call (0 = not yet drawn)

    _ANSI_STATUS = {
        "running":   "\033[33m",   # yellow
        "paused":    "\033[33m",
        "completed": "\033[32m",   # green
        "failed":    "\033[31m",   # red
    }
    _W = max(console.width, 40)

    def _draw(selected: int) -> None:
        nonlocal menu_height
        out: list[str] = []

        # ── Erase previous render (if any) ────────────────────────
        if menu_height > 0:
            out.append(f"\033[{menu_height}A\033[J")

        rows = 0

        # Separator rule
        out.append(f"\r\033[2m{'─' * _W}\033[0m\r\n")
        rows += 1

        # Header hint
        out.append(
            "\r  \033[2mSessions — ↑/↓ navigate · Enter to resume"
            " · Esc to cancel\033[0m\r\n"
        )
        rows += 1

        # Session rows
        for i, s in enumerate(sessions):
            st_ansi = _ANSI_STATUS.get(s.status, "\033[2m")
            phase = s.current_phase or s.next_incomplete_phase() or "done"
            task_preview = s.task[:40].replace("\n", " ")
            row_text = (
                f"  {st_ansi}{s.status:<10}\033[0m "
                f"\033[36m{s.session_id:<14}\033[0m "
                f"\033[2mphase:{phase:<10}\033[0m "
                f"\033[2m{task_preview}\033[0m"
            )
            if i == selected:
                # Bold white on blue, padded to terminal width
                plain = (
                    f"  {s.status:<10} {s.session_id:<14} "
                    f"phase:{phase:<10} {task_preview}"
                )
                out.append(f"\r\033[1;37;44m{plain:<{_W}}\033[0m\r\n")
            else:
                out.append(f"\r{row_text}\r\n")
            rows += 1

        # Prompt marker (no trailing newline — cursor stays on this line)
        out.append("\r\033[1;35m> \033[0m")

        sys.stdout.write("".join(out))
        sys.stdout.flush()
        menu_height = rows   # save line count for next erase

    def _erase() -> None:
        """Erase the picker and leave cursor on a clean line."""
        if menu_height > 0:
            sys.stdout.write(f"\033[{menu_height}A\033[J")
            sys.stdout.flush()

    _draw(sel)

    while True:
        ch = msvcrt.getwch()

        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            if ch2 == "H":      # ↑ Up
                sel = max(0, sel - 1)
                _draw(sel)
            elif ch2 == "P":    # ↓ Down
                sel = min(total - 1, sel + 1)
                _draw(sel)
            continue

        if ch in ("\r", "\n"):  # Enter — select and return
            _erase()
            return sessions[sel].session_id

        if ch in ("\x1b", "\x08", "\x03"):  # Esc / Backspace / Ctrl+C — cancel
            _erase()
            return None


# ── Attachment selection-mode rendering ────────────────────────────────

def _attachment_count() -> int:
    """Total number of pending attachments (pastes + images)."""
    return len(_attached_pastes) + len(_attached_images)


def _redraw_prompt_area(selected: int = -1) -> None:
    """Erase & redraw the full prompt area using scroll-safe relative movement.

    Parameters
    ----------
    selected : int
        Index into the combined attachment list to highlight.
        -1 means no selection (normal display).

    Must be called while the cursor is on the ``> `` prompt line (i.e. from
    within selection mode / _prompt_line_raw before a \\r\\n is written).

    Layout when called (N = _prompt_draw_badge_count):
        ...                 ← want cursor here, then erase ↓
        ── rule ──          (1 line)
        badge 1 … badge N  (N lines)
        > <cursor>          (prompt line — cursor is here)

    Lines to move up: N + 2  (rule + badges + prompt line we're sitting on)
    """
    global _prompt_draw_badge_count
    n_up = _prompt_draw_badge_count + 2
    sys.stdout.write(f"\033[{n_up}A\033[J")
    sys.stdout.flush()

    console.rule(style="bright_black")

    total = _attachment_count()
    idx = 0

    for num, plines in _attached_pastes:
        badge = f"\\[Pasted text #{num} +{len(plines)} lines]"
        if idx == selected:
            console.print(f"  [bold white on red] {badge} [/]")
        else:
            console.print(f"  [bold cyan on grey23] {badge} [/]")
        idx += 1

    for num, img_path in _attached_images:
        name = Path(img_path).name
        try:
            size = _humanize_size(Path(img_path).stat().st_size)
        except OSError:
            size = "?"
        if idx == selected:
            console.print(
                f"  [bold white on red] \\[Image #{num}] [/]"
                f" [dim]{name} ({size})[/]"
            )
        else:
            console.print(f"  {_image_badge(num, img_path)}")
        idx += 1

    # Hint on the last attachment line (only in normal mode)
    if total > 0 and selected == -1:
        # Move cursor up to end of last attachment line, append hint
        sys.stdout.write(f"\033[A\033[999C")  # up 1, end of line
        sys.stdout.write(" \033[2m(↑ to select)\033[0m")
        sys.stdout.write("\n")  # back down
        sys.stdout.flush()

    # Print prompt prefix
    sys.stdout.write("\033[1;35m> \033[0m")
    sys.stdout.flush()

    # Record how many badge lines were just drawn so the next erase/redraw
    # knows exactly how far up to move the cursor.
    _prompt_draw_badge_count = _attachment_count()


def _run_selection_mode() -> None:
    """Enter attachment selection mode (called from ``_prompt_line_raw``).

    ↑/↓ or ←/→ navigate, Backspace deletes selected, Escape/Enter exits.
    Redraws the prompt area after every action.
    """
    import msvcrt

    total = _attachment_count()
    if total == 0:
        return

    sel = total - 1  # start at last attachment
    _redraw_prompt_area(selected=sel)

    while True:
        ch = msvcrt.getwch()

        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            if ch2 == "K":      # Left
                sel = max(0, sel - 1)
                _redraw_prompt_area(selected=sel)
            elif ch2 == "M":    # Right
                sel = min(total - 1, sel + 1)
                _redraw_prompt_area(selected=sel)
            elif ch2 == "H":    # Up — move left (wraps to intent)
                sel = max(0, sel - 1)
                _redraw_prompt_area(selected=sel)
            elif ch2 == "P":    # Down — exit selection
                _redraw_prompt_area(selected=-1)
                return
            continue

        if ch == "\x08":  # Backspace — delete selected attachment
            if sel < len(_attached_pastes):
                _attached_pastes.pop(sel)
            else:
                _attached_images.pop(sel - len(_attached_pastes))

            total = _attachment_count()
            if total == 0:
                _redraw_prompt_area(selected=-1)
                return
            sel = min(sel, total - 1)
            _redraw_prompt_area(selected=sel)
            continue

        if ch in ("\x1b", "\r", "\n"):  # Escape or Enter — exit
            _redraw_prompt_area(selected=-1)
            return

        if ch == "\x03":  # Ctrl+C — exit
            _redraw_prompt_area(selected=-1)
            return


# ── Raw single-line input (Windows) ───────────────────────────────────

def _line_redraw_tail(buf: list[str], cursor: int) -> None:
    """Rewrite everything from *cursor* to end-of-buffer, clear one extra
    character (to erase a deleted char), then move the terminal cursor
    back to *cursor*."""
    tail = "".join(buf[cursor:])
    sys.stdout.write(tail + " ")            # overwrite + clear one
    move_back = len(buf) - cursor + 1
    if move_back > 0:
        sys.stdout.write(f"\033[{move_back}D")  # return to cursor pos
    sys.stdout.flush()


def _prompt_line_raw(prompt_ansi: str, primary: bool = False,
                     initial_text: str = ""):
    """Read one line using raw keypresses (Windows ``msvcrt``).

    Supports full cursor movement (←/→, Home, End, Delete) so the prompt
    feels like a normal shell.  When *primary* is True the slash-command
    menu and ↑-to-select attachment mode are enabled.

    *initial_text* pre-fills the buffer (used to restore text that was in
    the prompt when an image/paste was auto-attached mid-typing).

    Returns
    -------
    (text, paste_lines, action)
        *text*        – the typed string.
        *paste_lines* – ``list[str]`` of **all** pasted lines when a
                        multi-line paste is detected, otherwise ``None``.
        *action*      – ``'submit'`` | ``'ctrl-c'``.
    """
    import msvcrt

    sys.stdout.write(prompt_ansi)
    if initial_text:
        sys.stdout.write(initial_text)
    sys.stdout.flush()

    buf: list[str] = list(initial_text)
    cursor = len(initial_text)    # position inside buf
    menu_n = 0                    # slash-menu lines currently displayed
    menu_sel = -1                 # currently highlighted slash-menu row (-1 = none)

    while True:
        ch = msvcrt.getwch()

        # ── Enter ─────────────────────────────────────────────────
        if ch in ("\r", "\n"):
            # If a menu item is highlighted, fill it in (and submit if no args needed).
            if primary and menu_n and menu_sel >= 0:
                text = "".join(buf)
                base = text.split()[0] if text.split() else text
                matches = [(c, d) for c, d in _SLASH_COMMANDS
                           if c.split()[0].startswith(base)]
                if 0 <= menu_sel < len(matches):
                    sel_cmd, _ = matches[menu_sel]
                    keyword = sel_cmd.split()[0]        # e.g. "/ask"
                    needs_args = "<" in sel_cmd         # e.g. "/ask <question>"
                    _clear_below_cursor(menu_n, 0)
                    menu_n = 0
                    menu_sel = -1
                    if needs_args:
                        # Fill keyword + space into buffer so the user can add the argument.
                        buf = list(keyword + " ")
                        cursor = len(buf)
                        sys.stdout.write(f"\r{prompt_ansi}{keyword} \033[K")
                        sys.stdout.flush()
                        continue
                    else:
                        # No arguments needed — submit immediately.
                        sys.stdout.write("\r\n")
                        sys.stdout.flush()
                        return (keyword, None, "submit")
            if menu_n:
                _clear_below_cursor(menu_n, 0)
                menu_n = 0
            sys.stdout.write("\r\n")
            sys.stdout.flush()

            text = "".join(buf)

            # Paste detection: any data left in the buffer after Enter?
            time.sleep(0.03)
            if msvcrt.kbhit():
                all_lines = [text]
                current: list[str] = []
                while msvcrt.kbhit():
                    c = msvcrt.getwch()
                    if c in ("\r", "\n"):
                        all_lines.append("".join(current))
                        current = []
                    else:
                        current.append(c)
                if current:
                    all_lines.append("".join(current))
                while all_lines and not all_lines[-1].strip():
                    all_lines.pop()
                return (text, all_lines, "submit")

            return (text, None, "submit")

        # ── Ctrl+C ────────────────────────────────────────────────
        if ch == "\x03":
            if menu_n:
                _clear_below_cursor(menu_n, 0)
            sys.stdout.write("\r\n")
            sys.stdout.flush()
            return ("", None, "ctrl-c")

        # ── Backspace ─────────────────────────────────────────────
        if ch == "\x08":
            if cursor > 0:
                buf.pop(cursor - 1)
                cursor -= 1
                sys.stdout.write("\033[D")          # move left 1
                _line_redraw_tail(buf, cursor)
                if primary:
                    text = "".join(buf)
                    col = cursor + 3
                    if text.startswith("/"):
                        menu_sel = -1
                        menu_n = _render_slash_menu(text, menu_n, col)
                    elif menu_n:
                        _clear_below_cursor(menu_n, col)
                        menu_n = 0
                        menu_sel = -1
            continue

        # ── Special keys (arrows, Home, End, Delete) ──────────────
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()

            if ch2 == "K":          # ← Left
                if cursor > 0:
                    cursor -= 1
                    sys.stdout.write("\033[D")
                    sys.stdout.flush()

            elif ch2 == "M":        # → Right
                if cursor < len(buf):
                    cursor += 1
                    sys.stdout.write("\033[C")
                    sys.stdout.flush()

            elif ch2 == "H":        # ↑ Up
                if primary and menu_n:
                    text = "".join(buf)
                    base = text.split()[0] if text.split() else text
                    n_m = sum(1 for c, _ in _SLASH_COMMANDS if c.split()[0].startswith(base))
                    if n_m:
                        menu_sel = (menu_sel - 1) % n_m
                        menu_n = _render_slash_menu(text, menu_n, cursor + 3, sel=menu_sel)
                elif primary and not buf and _attachment_count() > 0:
                    _run_selection_mode()
                    # selection mode redraws with `> ` — keep reading
                # else: no-op (no history implemented)

            elif ch2 == "P":        # ↓ Down
                if primary and menu_n:
                    text = "".join(buf)
                    base = text.split()[0] if text.split() else text
                    n_m = sum(1 for c, _ in _SLASH_COMMANDS if c.split()[0].startswith(base))
                    if n_m:
                        menu_sel = (menu_sel + 1) % n_m
                        menu_n = _render_slash_menu(text, menu_n, cursor + 3, sel=menu_sel)

            elif ch2 == "G":        # Home
                if cursor > 0:
                    sys.stdout.write(f"\033[{cursor}D")
                    cursor = 0
                    sys.stdout.flush()

            elif ch2 == "O":        # End
                if cursor < len(buf):
                    sys.stdout.write(f"\033[{len(buf) - cursor}C")
                    cursor = len(buf)
                    sys.stdout.flush()

            elif ch2 == "S":        # Delete
                if cursor < len(buf):
                    buf.pop(cursor)
                    _line_redraw_tail(buf, cursor)

            continue

        # ── Tab (autocomplete slash command) ──────────────────────
        if ch == "\t":
            if primary and buf:
                text = "".join(buf)
                base = text.split()[0] if text.split() else text
                if base.startswith("/"):
                    hits = [c.split()[0] for c, _ in _SLASH_COMMANDS
                            if c.split()[0].startswith(base)]
                    # Use highlighted item if one is selected, else fall back
                    # to the only match (original behaviour).
                    if 0 <= menu_sel < len(hits):
                        target = hits[menu_sel]
                    elif len(hits) == 1:
                        target = hits[0]
                    else:
                        target = None
                    if target:
                        comp = target[len(base):]
                        if comp:
                            extra = comp + " "
                            for c in extra:
                                buf.insert(cursor, c)
                                cursor += 1
                            sys.stdout.write(extra)
                            sys.stdout.flush()
                        menu_sel = -1
                        menu_n = _render_slash_menu("".join(buf), menu_n, cursor + 3, sel=menu_sel)
            continue

        # ── Escape ────────────────────────────────────────────────
        if ch == "\x1b":
            if menu_n:
                _clear_below_cursor(menu_n, cursor + 3)
                menu_n = 0
                menu_sel = -1
            continue

        # ── Regular printable character ───────────────────────────
        if ord(ch) < 32:
            continue

        buf.insert(cursor, ch)
        cursor += 1
        # Write new char + everything after, then reposition cursor
        sys.stdout.write(ch + "".join(buf[cursor:]))
        if cursor < len(buf):
            sys.stdout.write(f"\033[{len(buf) - cursor}D")
        sys.stdout.flush()

        # ── Paste / drag-and-drop burst detection ─────────────────
        # When more characters are immediately available the input
        # is a paste or drag-and-drop, not manual typing.  Collect
        # the entire burst, then check whether the result is an
        # image path and, if so, auto-submit without requiring Enter.
        if primary and msvcrt.kbhit():
            pre_burst_cursor = cursor
            time.sleep(0.025)           # let the burst fully arrive
            got_enter = False
            _past_first_nl = False          # have we crossed the first newline?
            _burst_extra_lines: list[str] = []   # lines after the first newline
            _burst_cur_extra: list[str] = []     # chars for current extra line
            while msvcrt.kbhit():
                c = msvcrt.getwch()
                if c in ("\r", "\n"):
                    if not _past_first_nl:
                        got_enter = True
                        _past_first_nl = True
                    else:
                        _burst_extra_lines.append("".join(_burst_cur_extra))
                        _burst_cur_extra = []
                elif ord(c) >= 32:
                    if not _past_first_nl:
                        buf.insert(cursor, c)
                        cursor += 1
                    else:
                        _burst_cur_extra.append(c)
            if _burst_cur_extra:
                _burst_extra_lines.append("".join(_burst_cur_extra))
            # Repaint the whole prompt line cleanly
            sys.stdout.write(f"\r{prompt_ansi}{''.join(buf)}\033[K")
            sys.stdout.flush()
            candidate = "".join(buf)

            # Extract the full pasted/dropped portion.
            # IMPORTANT: the regular char handler consumed the first character
            # of the paste BEFORE the burst fired, so the pasted region begins
            # one position earlier than pre_burst_cursor.
            pasted_start = pre_burst_cursor - 1
            pasted_part = "".join(buf[pasted_start:cursor])
            # True only when the user had typed text before the paste started.
            had_prior_text = pasted_start > 0

            # Check if the PASTED portion alone is an image path
            if _is_image_path(pasted_part):
                if had_prior_text:
                    # User had typed text before the drag-drop.
                    # Strip the *entire* image path from the buffer (including
                    # the first char that leaked in before the burst), keeping
                    # the original text intact, and auto-attach.
                    del buf[pasted_start:cursor]
                    cursor = pasted_start
                    _try_attach_image(pasted_part)
                    img_path = _attached_images[-1][1]
                    img_num = _attached_images[-1][0]
                    # Redraw: prompt + original text, then badge below
                    sys.stdout.write(f"\r{prompt_ansi}{''.join(buf)}\033[K\r\n")
                    badge_name = Path(img_path).name
                    try:
                        badge_size = _humanize_size(Path(img_path).stat().st_size)
                    except OSError:
                        badge_size = ""
                    size_s = f" ({badge_size})" if badge_size else ""
                    sys.stdout.write(
                        f"  \033[1;36;48;5;237m [Image #{img_num}] \033[0m"
                        f" \033[2m{badge_name}{size_s}\033[0m\r\n"
                    )
                    # Reprint the prompt with the preserved text
                    sys.stdout.write(f"{prompt_ansi}{''.join(buf)}")
                    if cursor < len(buf):
                        sys.stdout.write(f"\033[{len(buf) - cursor}D")
                    sys.stdout.flush()
                    # User stays on prompt with their text intact
                    continue
                else:
                    # No prior text — original auto-submit behavior
                    if menu_n:
                        _clear_below_cursor(menu_n, 0)
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    return (pasted_part, None, "submit")

            # Fallback: check full buffer when no prior text was typed
            # (handles the case where pasted_part check above didn't fire)
            if not had_prior_text and _is_image_path(candidate):
                if menu_n:
                    _clear_below_cursor(menu_n, 0)
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return (candidate, None, "submit")

            # Enter arrived inside the paste — submit as normal line
            if got_enter:
                if menu_n:
                    _clear_below_cursor(menu_n, 0)
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                # Multi-line paste: return all lines so _prompt_task
                # can attach them as a [Pasted text #N +M lines] badge.
                if _burst_extra_lines:
                    all_lines = [candidate] + _burst_extra_lines
                    while all_lines and not all_lines[-1].strip():
                        all_lines.pop()
                    if len(all_lines) > 1:
                        # For pure pastes (no prior typed text), return empty
                        # text so _prompt_task doesn't treat the first paste
                        # line as typed instructions and pre-fill it via
                        # _pending_text on the next iteration.
                        ret_text = candidate if had_prior_text else ""
                        return (ret_text, all_lines, "submit")
                return (candidate, None, "submit")

        if primary:
            text = "".join(buf)
            col = cursor + 3
            if text.startswith("/"):
                menu_sel = -1
                menu_n = _render_slash_menu(text, menu_n, col)
            elif menu_n:
                _clear_below_cursor(menu_n, col)
                menu_n = 0
                menu_sel = -1


# ── Fallback single-line input (non-Windows) ──────────────────────────

def _prompt_line_fallback(prompt_markup: str):
    """Read one line with ``console.input`` (no special key handling)."""
    try:
        text = console.input(prompt_markup)
    except EOFError:
        return ("", None, "submit")

    stripped = text.strip()
    time.sleep(0.05)
    if _has_stdin_data():
        remaining = _drain_stdin_lines()
        return (text, [text] + remaining, "submit")
    return (text, None, "submit")


# ── Full prompt ───────────────────────────────────────────────────────

def _prompt_task() -> str:
    """Multiline task prompt.

    * Pasted text collapses into a ``[Pasted text #N +M lines]`` badge and
      is stored as an attachment — the prompt loops back so the user can
      type instructions that reference the paste.
    * ``/image <path>`` or drag-and-drop attaches images.
    * ↑ on empty prompt enters **selection mode**: ←/→ to navigate
      attachments, Backspace to delete the selected one, Esc/Enter to exit.
    * Typing ``/`` shows an interactive slash-command picker with Tab
      completion.
    * Manually typed multi-line input: empty line (double-Enter) submits.
    """
    global _paste_counter

    use_raw = os.name == "nt"

    # Text the user had typed when an image/paste was auto-attached.
    # Restored as initial_text on the very next prompt call so it isn't lost.
    _pending_text: str = ""

    while True:
        lines: list[str] = []

        # ── Record badge count before drawing the prompt area ────
        # _erase_screen_from and _redraw_prompt_area use this to compute
        # how far to move the cursor upward (scroll-safe relative movement).
        global _prompt_draw_badge_count
        total_att = _attachment_count()
        _prompt_draw_badge_count = total_att

        # Dim horizontal rule above prompt
        console.rule(style="bright_black")

        # Show pending attachments with hint
        has_attachments = total_att > 0
        idx = 0
        for num, plines in _attached_pastes:
            badge = f"\\[Pasted text #{num} +{len(plines)} lines]"
            hint = " [dim](↑ to select)[/]" if idx == total_att - 1 else ""
            console.print(
                f"  [bold cyan on grey23] {badge} [/]{hint}"
            )
            idx += 1
        for num, img_path in _attached_images:
            hint = " [dim](↑ to select)[/]" if idx == total_att - 1 else ""
            console.print(f"  {_image_badge(num, img_path)}{hint}")
            idx += 1

        try:
            # ── Read first line ──────────────────────────────────
            if use_raw:
                text, paste, action = _prompt_line_raw(
                    "\033[1;35m> \033[0m", primary=True,
                    initial_text=_pending_text,
                )
            else:
                text, paste, action = _prompt_line_fallback(
                    "[bold magenta]> [/]",
                )
            _pending_text = ""   # consumed (or unused on fallback path)

            if action == "ctrl-c":
                # Forward to the double-press Ctrl+C handler directly
                # (signal.SIGINT doesn't fire when msvcrt eats \x03).
                _sigint_handler(None, None)
                continue

            stripped = text.strip()

            # ── Slash commands ────────────────────────────────────
            if stripped.lower().startswith("/image"):
                _handle_image_command(stripped)
                continue
            if stripped.lower() in ("/clear-images", "/clear-paste", "/clear"):
                if stripped.lower() in ("/clear-images", "/clear"):
                    _attached_images.clear()
                if stripped.lower() in ("/clear-paste", "/clear"):
                    _attached_pastes.clear()
                console.print("[dim]  Attachments cleared.[/]")
                continue
            if stripped.lower() == "/auto":
                new_state = not is_auto_all()
                set_auto_all(new_state)
                state_str = "ON" if new_state else "OFF"
                console.print(f"[bold cyan]  Auto-approve mode: {state_str}[/]")
                continue
            if stripped.lower() == "/sessions":
                sid = _run_session_picker()
                if sid:
                    return f"__RESUME__{sid}"
                continue
            if stripped.lower().startswith("/resume"):
                parts = stripped.split(maxsplit=1)
                if len(parts) > 1 and parts[1].strip():
                    return f"__RESUME__{parts[1].strip()}"
                console.print("[dim]  Usage: /resume <session-id>[/]")
                continue
            if stripped.lower() == "/pause":
                if _current_session and _current_work_dir:
                    _current_session.status = "paused"
                    save_session(_current_work_dir, _current_session)
                    console.print(
                        f"[bold yellow]Session [cyan]{_current_session.session_id}[/cyan] "
                        f"paused and saved.  Resume with:[/]  "
                        f"unicode --resume {_current_session.session_id}"
                    )
                else:
                    console.print("[dim]  No active session to pause.[/]")
                return "__PAUSE__"
            if stripped.lower() == "/stop":
                console.print("[bold red]Stopping orchestrator.[/]")
                return "__STOP__"
            if stripped.lower().startswith("/ask"):
                parts = stripped.split(maxsplit=1)
                if len(parts) > 1 and parts[1].strip():
                    return f"__ASK__{parts[1].strip()}"
                console.print("[dim]  Usage: /ask <question>[/]")
                continue
            if stripped.lower() == "/init":
                return "__INIT__"

            # ── Auto-detect image path ────────────────────────────
            # Pure heuristic: looks like a path + has image extension.
            # Works for typed text AND single-line pastes.
            if _try_attach_image(stripped):
                _erase_screen_from(saved=True)
                continue

            # ── Paste detected ────────────────────────────────────
            if paste:
                # If the user had typed instructions before the paste/image
                # arrived (via Enter+burst), save that text so the next
                # prompt iteration pre-fills it.  Don't save if stripped IS
                # itself an image path (that was already handled above).
                _saved = stripped if (stripped and not _is_image_path(stripped)) else ""

                # Multi-line drag-drop image path (e.g. & 'C:\...\img.png'\n)
                if _paste_is_image_path(paste):
                    _pending_text = _saved
                    _erase_screen_from(saved=True)
                    continue
                # Multi-line or non-image text → attach as paste badge.
                _paste_counter += 1
                _attached_pastes.append((_paste_counter, paste))
                _pending_text = _saved
                _erase_screen_from(saved=True)
                continue  # loop back — user types instructions next

            if not stripped:
                if _attached_pastes or _attached_images:
                    pass  # submit with just attachments
                else:
                    console.print("[dim]Please enter a task.[/]")
                    continue

            if stripped:
                lines.append(text)

            # ── Continuation lines (manual multiline) ────────────
            while True:
                if use_raw:
                    cont, cpaste, cact = _prompt_line_raw(
                        "\033[2m... \033[0m", primary=False,
                    )
                else:
                    cont, cpaste, cact = _prompt_line_fallback(
                        "[dim]... [/]",
                    )
                if cact == "ctrl-c":
                    _sigint_handler(None, None)
                    break

                cont_stripped = cont.strip()

                # Slash commands / images in continuation
                if cont_stripped.lower().startswith("/image"):
                    _handle_image_command(cont_stripped)
                    continue
                if _try_attach_image(cont_stripped):
                    continue

                # Paste on continuation line → attach it
                if cpaste:
                    # Multi-line drag-drop image path
                    if _paste_is_image_path(cpaste):
                        continue
                    _paste_counter += 1
                    _attached_pastes.append((_paste_counter, cpaste))
                    console.print(
                        f"  [bold cyan on grey23]"
                        f" \\[Pasted text #{_paste_counter}"
                        f" +{len(cpaste)} lines] [/]"
                    )
                    continue

                # Empty line → submit
                if not cont_stripped:
                    break
                lines.append(cont)

        except (EOFError, KeyboardInterrupt):
            if not lines and not _attached_pastes and not _attached_images:
                continue

        task = "\n".join(lines).strip()
        if not task and not _attached_pastes and not _attached_images:
            console.print("[dim]Please enter a task.[/]")
            continue

        # Colored gradient underline after submission
        console.print(_prompt_gradient_line())

        # Prepend pasted text attachments
        if _attached_pastes:
            for num, plines in _attached_pastes:
                paste_block = "\n".join(plines)
                task = (
                    f"[Pasted text #{num} — {len(plines)} lines]:\n"
                    f"{paste_block}\n\n{task}"
                )
            _attached_pastes.clear()

        # Prepend image attachments
        if _attached_images:
            img_lines = "\n".join(
                f"[Attached image #{num}: {path}]" for num, path in _attached_images
            )
            task = (
                f"{img_lines}\n"
                f"(Use the Read tool to view the attached images above.)\n\n"
                f"{task}"
            )
            _attached_images.clear()

        return task


def _load_saved_plan(work_dir: str) -> str:
    """Try to load a previously saved plan from .orchestrator/plan.md."""
    plan_path = Path(work_dir) / ".orchestrator" / "plan.md"
    if plan_path.exists():
        plan = plan_path.read_text(encoding="utf-8").strip()
        if plan:
            log_info(f"Loaded saved plan from {plan_path}")
            return plan
    log_info("No saved plan found — proceeding without one.")
    return ""


_COMPACT_THRESHOLD = 6_000   # chars — compact if file exceeds this
_COMPACT_TARGET = 3_000      # chars — aim for this after compaction
_MEMORY_LIST_MAX = 20    # max YAML list entries per category before pruning

_COMPACT_INSTRUCTIONS = {
    "bugs.md": (
        "This is a bug log. Keep the 5 most important and recent entries. "
        "For each, preserve: date, issue, root cause, solution, prevention. "
        "Merge entries that describe the same underlying problem. "
        "Drop entries that are too vague to be actionable."
    ),
    "decisions.md": (
        "This is an architectural decision log (ADRs). Keep the 5 most impactful decisions. "
        "Prefer decisions that constrain future work (technology choices, patterns, conventions). "
        "Drop decisions about one-off tasks or minor implementation details. "
        "Preserve the ADR-NNN numbering of kept entries."
    ),
    "key_facts.md": (
        "This is a project facts file. Merge duplicate categories. "
        "Within each category keep only the most recent and accurate fact. "
        "Drop outdated facts superseded by newer ones. "
        "Keep: tech stack, entry points, conventions, important URLs."
    ),
}


def _compact_memory_files(qwen: QwenAgent, codex: CodexAgent, work_dir: str) -> None:
    """Distill oversized .orchestrator/ markdown files.

    Tries Qwen first. If Qwen returns something larger than the original
    (or fails), falls back to Codex. If both fail, the file is left as-is.
    issues.md is excluded — it is a chronological log, not a lookup store.
    """
    notes_dir = Path(work_dir) / ".orchestrator"
    for filename, instructions in _COMPACT_INSTRUCTIONS.items():
        path = notes_dir / filename
        if not path.exists():
            continue
        try:
            if path.stat().st_size <= _COMPACT_THRESHOLD:
                continue
            content = path.read_text(encoding="utf-8")
            if len(content) <= _COMPACT_THRESHOLD:
                continue
        except OSError:
            continue

        prompt = (
            f"<role>You are compacting a project memory file that has grown too large.</role>\n\n"
            f"<context>\n"
            f"FILE: {filename}\n"
            f"CURRENT CONTENT ({len(content)} chars):\n{content}\n"
            f"</context>\n\n"
            f"<rules>\n"
            f"{instructions}\n"
            f"Rewrite the file keeping only the most important entries. "
            f"Target under {_COMPACT_TARGET} characters. "
            f"Keep the original markdown format and the top-level heading. "
            f"Add a line '> Compacted {datetime.now().strftime('%Y-%m-%d')} — older entries distilled.' "
            f"after the heading.\n"
            f"</rules>\n\n"
            f"Return ONLY the new file content, nothing else."
        )

        compacted = None
        # Priority-ordered fallback: Qwen first, Codex if Qwen returns a larger result
        for agent_name, query_fn in [("Qwen", qwen.query), ("Codex", codex.query)]:
            try:
                result = query_fn(prompt).strip()
                if result and len(result) < len(content):
                    compacted = result
                    log_info(f"Compacted {filename} via {agent_name}: {len(content)} → {len(result)} chars")
                    break
                else:
                    log_info(f"{agent_name} compaction of {filename} did not reduce size — trying fallback")
            except Exception:
                log_info(f"{agent_name} compaction of {filename} failed — trying fallback")

        if compacted:
            try:
                path.write_text(compacted + "\n", encoding="utf-8")
            except OSError:
                pass  # Never break the pipeline over memory housekeeping


def _synthesize_memory(
    qwen: QwenAgent,
    codex: CodexAgent,
    task: str,
    plan: str,
    review_text: str,
    outcome: str,
    work_dir: str,
) -> None:
    """Ask Qwen to extract real memory entries from the completed run and write them.

    Replaces the old mechanical raw-dump approach with actual synthesis.
    Writes to: memory.yaml, bugs.md, decisions.md, key_facts.md, issues.md.
    """
    prompt = (
        "You just completed a software task. Extract structured memory entries from the context below.\n\n"
        f"<task>{task[:400]}</task>\n\n"
        f"<context>\n"
        f"PLAN SUMMARY: {plan[:600]}\n\n"
        f"OUTCOME: {outcome}\n"
        f"</context>\n\n"
    )
    if review_text:
        prompt += f"<context>\nREVIEW FEEDBACK:\n{review_text[:600]}\n</context>\n\n"
    prompt += (
        "<output_format>\n"
        "Return a JSON object with these optional keys (omit any key if nothing real to record):\n"
        "{\n"
        '  "key_facts": [\n'
        '    {"category": "Tech Stack", "fact": "Uses FastAPI with async SQLAlchemy"}\n'
        "  ],\n"
        '  "decisions": [\n'
        "    {\n"
        '      "title": "Use async handlers for DB queries",\n'
        '      "context": "App needs concurrent request handling",\n'
        '      "decision": "All DB queries use async/await with SQLAlchemy async session",\n'
        '      "alternatives": "Sync with thread pool",\n'
        '      "consequences": "Better concurrency, careful session lifecycle needed"\n'
        "    }\n"
        "  ],\n"
        '  "bugs": [\n'
        "    {\n"
        '      "issue": "Race condition in session cleanup",\n'
        '      "root_cause": "Session not closed in finally block",\n'
        '      "solution": "Use async context manager pattern",\n'
        '      "prevention": "Always use context managers for DB sessions"\n'
        "    }\n"
        "  ],\n"
        '  "lessons": ["Specific lesson 1", "Specific lesson 2"],\n'
        '  "issue_notes": "One sentence summary of what was done and the result."\n'
        "}\n"
        "</output_format>\n\n"
        "<rules>\n"
        "- Only record REAL decisions made during THIS task, not generic advice.\n"
        "- Only record bugs actually found or fixed during this task.\n"
        "- key_facts should be specific to this project (ports, patterns, conventions found).\n"
        "- lessons should be concrete coding lessons (max 3, max 50 words each).\n"
        "- If nothing meaningful to record for a key, omit it entirely.\n"
        "</rules>\n"
        "Return ONLY valid JSON, no markdown fences, nothing else."
    )
    try:
        raw = qwen.query(prompt)
        data = parse_json_response(raw)
    except Exception:
        data = {}

    memory = load_memory(work_dir)

    # ── key_facts.md ─────────────────────────────────────────────────────────
    for entry in data.get("key_facts", []):
        if isinstance(entry, dict) and entry.get("fact"):
            log_key_fact(work_dir, entry.get("category", "General"), entry["fact"])

    # ── decisions.md + YAML architecture_decisions ───────────────────────────
    for dec in data.get("decisions", []):
        if isinstance(dec, dict) and dec.get("title") and dec.get("decision"):
            log_decision(
                working_dir=work_dir,
                title=dec["title"],
                context=dec.get("context", f"Task: {task[:200]}"),
                decision=dec["decision"],
                alternatives=dec.get("alternatives", ""),
                consequences=dec.get("consequences", f"Outcome: {outcome}"),
            )
            if isinstance(memory.get("architecture_decisions"), list):
                memory["architecture_decisions"].append({
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "text": f"{dec['title']}: {dec['decision'][:150]}",
                })

    # ── bugs.md + YAML past_mistakes ─────────────────────────────────────────
    for bug in data.get("bugs", []):
        if isinstance(bug, dict) and bug.get("issue"):
            log_bug(
                working_dir=work_dir,
                issue=bug["issue"],
                root_cause=bug.get("root_cause", ""),
                solution=bug.get("solution", ""),
                prevention=bug.get("prevention", ""),
            )
    for lesson in data.get("lessons", []):
        if isinstance(lesson, str) and lesson.strip():
            memory["past_mistakes"].append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "text": lesson.strip(),
            })

    # ── issues.md ────────────────────────────────────────────────────────────
    issue_notes = data.get("issue_notes", "")
    log_issue(work_dir, task, outcome, notes=issue_notes)

    # ── YAML task_index ───────────────────────────────────────────────────────
    keywords = extract_keywords_from_task(task)
    memory["task_index"].append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "task": task[:200],
        "outcome": outcome,
        "keywords": keywords,
    })

    # Prune and save YAML
    save_memory(work_dir, memory)
    log_info("Memory synthesized and saved to .orchestrator/")

    # Compact oversized markdown files so they never grow unboundedly
    _compact_memory_files(qwen, codex, work_dir)


def _run_task(
    task: str,
    cfg: dict,
    work_dir: str,
    claude: ClaudeAgent,
    codex: CodexAgent,
    qwen: QwenAgent,
    phase: str = "all",
    tier: str = "standard",
    session: Session | None = None,
    dry_run: bool = False,
) -> None:
    """Execute one full orchestration run for the given task.

    If *session* is provided the task resumes from the last completed
    checkpoint — phases that already have a stored result are skipped.
    """
    global _current_session, _current_work_dir

    # Reset per-session approvals for each new task
    reset_session_approvals()

    # ── Session bookkeeping ──
    if session is None:
        session = Session(task=task, tier=tier, cfg=cfg)
    session.status = "running"
    save_session(work_dir, session)
    _current_session = session
    _current_work_dir = work_dir

    transcript_path = init_transcript(work_dir)
    log_info(f"Transcript: {transcript_path}")
    log_info(f"Task: {task}")
    log_info(f"Session: {session.session_id}")
    log_info(f"Tier: {tier} | Dev model: {claude.dev_model}")
    start_time = time.time()

    # Enable prompt audit logging for all agents
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    for agent in [claude, codex, qwen]:
        agent.enable_audit(work_dir, run_id)

    def _set_phase(phase: str) -> None:
        """Set current phase on session and all agents in one call."""
        session.current_phase = phase
        for _a in [claude, codex, qwen]:
            _a.set_phase(phase)

    # Load shared memory context for this task (cached once, reused by all phases)
    memory_context = get_context_for_task(work_dir, task)
    if memory_context:
        log_info("Loaded shared memory context from previous tasks.")
        log_memory_context(memory_context)

    # Generate repo skeleton map (compressed AST-like view of the codebase)
    repo_map = generate_repo_map(work_dir, cfg.get("repo_map_max_tokens", 2000))
    if repo_map:
        log_info("Generated repo skeleton map.")

    # Track state across phases
    discussion: list[dict[str, str]] = []
    structured_plan = None
    plan = ""
    approved = False
    skip_to_review = False
    review_text = ""

    # ── Restore completed phases from session ──
    if session.phase_done("discussion"):
        disc_data = session.phases["discussion"]
        if isinstance(disc_data, dict):
            discussion = disc_data.get("discussion", [])
        log_info("Restored discussion from saved session.")

    if session.phase_done("plan"):
        plan_data = session.phases["plan"]
        plan = plan_data.get("plan", "")
        log_info("Restored plan from saved session.")

    # For standalone implement/review, load saved plan
    if phase in ("implement", "review") and not plan:
        plan = _load_saved_plan(work_dir)

    # ── Phase 0.5: Research (3 agents in parallel → Haiku synthesizes) ──
    if session.phase_done("research"):
        enriched = session.phases.get("research", {}).get("enriched_task", "")
        if enriched:
            task = enriched
            log_info("Restored enriched task from saved research session.")
    elif phase in ("all", "plan", "discuss"):
        if phase == "all":
            _print_phase_banner(
                "Research", "Codex × 2 + Qwen",
                "Codex (×2) · Qwen (web) research in parallel → Haiku synthesizes",
                "cyan",
            )
        _set_phase("research")
        save_session(work_dir, session)
        result, _ = request_approval(
            "research",
            "Two Codex instances and Qwen (DuckDuckGo web search) will research the task in parallel. "
            "Haiku will distill their findings into background context prepended to the task prompt.",
        )
        if result == "proceed":
            # Haiku is sufficient for distillation (no editorial judgment needed)
            synthesizer = ClaudeAgent(
                model="claude-haiku-4-5-20251001",
                timeout=cfg.get("timeout_seconds", 120),
                working_dir=work_dir,
            )
            wall = cfg.get("research_wall_seconds", 180)
            enriched = _run_phase("Research", run_research, task, codex, qwen, synthesizer,
                                  wall_seconds=wall)
            if enriched and enriched != task:
                task = enriched
            log_phase_outcome("research", {"brief": task})
        else:
            log_info("Skipping research phase.")
        session.mark_phase_done("research", {"enriched_task": task})
        save_session(work_dir, session)

    # ── Phase 1: Discussion (Claude + Codex agree on the approach) ──
    run_discuss = phase in ("all", "plan", "discuss") and not session.phase_done("discussion")
    if run_discuss:
        if phase == "all":
            _print_phase_banner("Discussion", "admins", "Claude & Codex will agree on the approach", "cyan")
        _set_phase("discussion")
        save_session(work_dir, session)
        disc_rounds = cfg.get("discussion_rounds", 2)
        if not is_auto_all():
            try:
                disc_rounds = click.prompt(
                    click.style("How many discussion rounds?", fg="cyan", bold=True),
                    default=disc_rounds,
                    type=click.IntRange(1, 10),
                )
            except (EOFError, click.Abort):
                pass
        result, extra = request_approval("discussion",
            f"Codex and Claude will discuss the task for up to {disc_rounds} rounds "
            f"(Codex goes first), stopping early once both agree.")
        agreed = False
        if result == "proceed":
            if extra:
                task = f"{task}\n\nADDITIONAL USER INSTRUCTIONS:\n{extra}"
                log_info("Updated task with your instructions.")
            disc_result = _run_phase("Discussion",
                run_discussion, task, claude, codex, disc_rounds,
                allow_user_questions=cfg.get("allow_user_questions", True),
                repo_map=repo_map)
            if disc_result is not None:
                discussion, agreed = disc_result
        else:
            log_info("Skipping discussion.")
        log_phase_outcome("discussion", {"discussion": discussion})
        session.mark_phase_done("discussion", {"discussion": discussion, "agreed": agreed})
        save_session(work_dir, session)

    # ── Phase 2: Plan (Codex writes the agreed plan) ──
    run_plan = phase in ("all", "plan", "discuss") and not session.phase_done("plan")
    if run_plan:
        if phase == "all":
            _print_phase_banner("Planning", "Codex", "Codex will write the agreed plan", "cyan")
        _set_phase("plan")
        save_session(work_dir, session)
        result, extra = request_approval("plan",
            "Codex will write the implementation plan based on the discussion.")
        if result == "proceed":
            if extra:
                task = f"{task}\n\nADDITIONAL USER INSTRUCTIONS:\n{extra}"
                log_info("Updated task with your instructions.")
            plan = _run_phase("Plan",
                consolidate_plan, task, codex, work_dir, discussion,
                memory_context=memory_context,
                repo_map=repo_map) or ""
            # Parse structured plan for file-by-file generation
            structured_plan = parse_plan(plan)
            if is_structured(structured_plan):
                log_info(f"Structured plan parsed: {len(structured_plan.files)} file specs")
                log_phase_outcome("plan", {"files": structured_plan.files})
            else:
                log_info("Plan is unstructured — will use monolithic implementation.")
                log_phase_outcome("plan", {"files": []})
        else:
            log_info("Skipping plan phase.")
        session.mark_phase_done("plan", {"plan": plan})
        save_session(work_dir, session)

    # Stop here if the user only wanted plan or discuss
    if phase in ("plan", "discuss"):
        log_phase("Phase complete.")
        duration = time.time() - start_time
        log_info(f"Finished in {format_duration(duration)}.")
        session.status = "completed"
        save_session(work_dir, session)
        _current_session = None
        return

    # ── Dry-run: stop before implementation ──────────────────────────────────
    if dry_run and plan:
        if is_structured(structured_plan):
            lines = [f"  {f.action}  {f.path}" for f in structured_plan.files]
            body = "\n".join(lines) or "(no files parsed)"
        else:
            body = "(monolithic plan — no file list available)"
        console.print(Panel(
            body,
            title="[bold yellow]DRY RUN: Would implement these files[/]",
            border_style="yellow",
        ))
        log_info("Dry-run mode — stopping before implementation.")
        return

    # ── Phase 3: Implementation (Claude as developer, with Qwen available) ──
    run_impl = (
        phase in ("all", "implement")
        and not session.phase_done("implement")
    )
    if run_impl and phase == "all":
        _print_phase_banner(
            "Implementation", "developer",
            f"Claude Code (dev:{claude.dev_model}) will implement the plan",
            "magenta",
        )
    if run_impl:
        _set_phase("implement")
        save_session(work_dir, session)
        result, extra = request_approval("implement",
            f"Claude (dev:{claude.dev_model}) will now implement the plan with full file access.")
        if result == "proceed":
            if extra:
                plan = f"{plan}\n\nADDITIONAL USER INSTRUCTIONS:\n{extra}"
                log_info("Updated plan with your instructions.")
            try:
                impl = _run_phase("Implementation",
                    run_implementation, task, plan, claude,
                    discussion=discussion,
                    memory_context=memory_context,
                    repo_map=repo_map,
                    structured_plan=structured_plan,
                    qwen=qwen,
                    work_dir=work_dir)
                if impl is not None:
                    # Qwen writes orchestrator.md (project summary)
                    _run_phase("Writing orchestrator.md",
                        write_orchestrator_md, work_dir, task, plan, discussion, qwen)
            except TimeoutSkipToReview:
                log_info("Skipping to review phase (user request after timeout).")
                skip_to_review = True
        else:
            log_info("Skipping implementation phase.")
        session.mark_phase_done("implement", True)
        save_session(work_dir, session)

    # Stop here if the user only wanted implement (and didn't skip to review)
    if phase == "implement" and not skip_to_review:
        log_phase("Implementation phase complete.")
        duration = time.time() - start_time
        log_info(f"Finished in {format_duration(duration)}.")
        session.status = "completed"
        save_session(work_dir, session)
        _current_session = None
        return

    # ── Phase 4: Code Review (Codex reviews with Claude validation) ──
    if not session.phase_done("review"):
        _set_phase("review")
        save_session(work_dir, session)
        if phase == "all":
            _print_phase_banner("Code Review", "reviewer",
                "Part 1: Codex reviews  →  Part 2: Claude validates", "green")
        log_info("First code review is mandatory.")
        rev = _run_phase("Code Review",
            run_review, task, plan, claude, codex, work_dir,
            cfg["max_review_iterations"])
        if rev is None:
            approved, review_text = True, ""
        elif isinstance(rev, tuple):
            approved, review_text = rev
        else:
            approved, review_text = bool(rev), ""
        log_phase_outcome("review", {
            "verdict": "APPROVED" if approved else "CHANGES_REQUESTED",
            "issue_count": 0,
        })
        session.mark_phase_done("review", approved)
        save_session(work_dir, session)
    else:
        approved = session.phases["review"]
        log_info("Restored review result from saved session.")

    # ── Phase 5: Finalization ──
    _set_phase("finalize")
    save_session(work_dir, session)
    log_phase("Phase 5: Finalization")
    outcome = "APPROVED" if approved else "NOT APPROVED"

    if approved:
        log_success("Implementation approved!")

        # Codex (GPT) synthesizes and updates both agent MD files
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_claude = pool.submit(_run_phase, "Codex updating CLAUDE.md",
                agent_update_md, work_dir, task, plan, discussion, codex, "CLAUDE.md")
            f_agents = pool.submit(_run_phase, "Codex updating AGENTS.md",
                agent_update_md, work_dir, task, plan, discussion, codex, "AGENTS.md")
            concurrent.futures.wait([f_claude, f_agents])

        # Codex writes the commit message
        commit_prompt = (
            "<task>\n"
            f"Write a git commit message for these changes based on this task:\n{task}\n"
            "</task>\n\n"
            f"<plan>{plan[:1000]}</plan>\n\n"
            "<rules>\n"
            "- One line, max 72 characters\n"
            "- No quotes, no prefix\n"
            "- Just describe what was done\n"
            "</rules>"
        )
        log_info("Codex is writing commit message ...")
        commit_msg = _run_phase("Commit message", codex.query, commit_prompt)
        if not commit_msg:
            commit_msg = f"orchestrator: {task[:72]}"
        # Clean up — take first line only, strip quotes
        commit_msg = commit_msg.strip().split("\n")[0].strip('"').strip("'").strip()

        # User approves the commit message
        result, extra = request_approval("git-commit",
            f"Commit & push with message:\n\"{commit_msg}\"")
        if result == "proceed":
            if extra:
                commit_msg = extra  # user rewrote the message
            commit(commit_msg, work_dir)
            log_success(f"Changes committed: {commit_msg}")
            push(work_dir)
            log_success("Pushed to remote.")
        else:
            log_info("Commit skipped — changes are in your working directory.")
    else:
        log_error("Implementation was NOT approved after all review iterations.")

    # Record history (Qwen summarizes, not Claude)
    duration = time.time() - start_time
    summary_prompt = (
        f"<task>{task}</task>\n\n"
        f"<plan>{plan}</plan>\n\n"
        "<rules>\n"
        "List files created/modified as a bullet list. One line each. No commentary.\n"
        "</rules>"
    )
    log_info("Qwen is summarizing actions ...")
    actions_summary = _run_phase("Summary", qwen.query, summary_prompt)
    if not actions_summary:
        actions_summary = "- Summary skipped"

    transcript_name = transcript_path.name
    append_history(work_dir, task, outcome, duration, actions_summary, transcript_name)
    log_info("Appended run to .orchestrator/history.md")

    # ── Synthesize and save memory (Qwen extracts real entries) ──
    log_info("Qwen is synthesizing memory entries ...")
    _synthesize_memory(qwen, codex, task, plan, review_text, outcome, work_dir)

    # ── Verify memory was written ──
    _mem_after = load_memory(work_dir)
    _counts = {k: len(_mem_after.get(k, [])) for k in
               ["architecture_decisions", "past_mistakes", "task_index"]}
    if sum(_counts.values()) == 0:
        log_error("WARNING: Memory synthesis wrote 0 entries — memory.yaml may be empty.")
    else:
        log_phase_outcome("memory", {"counts": _counts})

    # ── Mark session complete ──
    session.mark_phase_done("finalize", outcome)
    session.status = "completed"
    save_session(work_dir, session)
    _current_session = None

    if approved:
        log_success("Task complete!")
    else:
        log_error("Task finished — implementation was not approved.")


@click.command()
@click.argument("task", required=False, default=None)
@click.option("--config", "config_path", default="config.yaml", help="Config file path.")
@click.option("--rounds", type=int, default=None, help="Override discussion rounds.")
@click.option("--auto-commit", is_flag=True, default=None, help="Auto-commit on approval.")
@click.option("--no-questions", is_flag=True, default=False, help="Disable admin questions to user during discussion.")
@click.option("--working-dir", default=None, help="Override working directory.")
@click.option(
    "--phase", default="all",
    type=click.Choice(["all", "plan", "discuss", "implement", "review"], case_sensitive=False),
    help="Run only a specific phase.",
)
@click.option(
    "--tier", default=None,
    type=click.Choice(["quick", "standard", "complex"], case_sensitive=False),
    help="Task complexity tier (skip selection prompt).",
)
@click.option("--auto", "auto_mode", is_flag=True, default=False,
    help="Auto-approve all phases except git commit.")
@click.option("--dev-model", default=None,
    help="Override developer model (e.g. sonnet, opus).")
@click.option("--resume", "resume_id", default=None,
    help="Resume a saved session by ID.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
    help="Run planning phases only; show files that would be implemented without writing.")
def main(
    task: str | None,
    config_path: str,
    rounds: int | None,
    auto_commit: bool | None,
    no_questions: bool,
    working_dir: str | None,
    phase: str,
    tier: str | None,
    auto_mode: bool,
    dev_model: str | None,
    resume_id: str | None,
    dry_run: bool,
):
    """Orchestrate Claude Code and Codex to collaboratively complete TASK."""
    # Install double Ctrl+C handler
    signal.signal(signal.SIGINT, _sigint_handler)

    cfg = load_config(config_path)

    # CLI overrides
    if rounds is not None:
        cfg["discussion_rounds"] = rounds
    if auto_commit is not None:
        cfg["auto_commit"] = auto_commit
    if no_questions:
        cfg["allow_user_questions"] = False
    if working_dir is not None:
        cfg["working_directory"] = working_dir
    if dev_model is not None:
        cfg["dev_model"] = dev_model

    work_dir = os.path.abspath(cfg["working_directory"])
    os.makedirs(work_dir, exist_ok=True)

    # Ensure git repo exists
    if not is_git_repo(work_dir):
        init_repo(work_dir)
        log_info(f"Initialized git repo in {work_dir}")

    # Initialize agent MD files (header + orchestrator.md reference)
    init_agent_md(work_dir)

    # Initialize .orchestrator/ memory files (project-memory skill)
    init_project_notes(work_dir)

    # Print the banner with info box
    _print_banner(cfg, work_dir)

    # Make work_dir available for session listing
    global _current_work_dir
    _current_work_dir = work_dir

    # Auto mode from CLI
    if auto_mode:
        set_auto_all(True)
        console.print("[bold cyan]Auto-approve mode: ON[/] [dim](git commit still requires confirmation)[/]")

    # Show phase banner immediately on startup when a specific phase is selected
    if phase != "all":
        _phase_banners = {
            "plan":      ("Planning",       "admins",   "Claude & Codex will draft the plan",      "cyan"),
            "discuss":   ("Discussion",     "admins",   "Claude & Codex will discuss the plan",    "cyan"),
            "implement": ("Implementation", "developer","Claude Code (developer) will implement",  "magenta"),
            "review":    ("Code Review",    "reviewer", "Codex reviews (Part 1), Claude validates (Part 2)", "green"),
        }
        label, role, desc, color = _phase_banners.get(phase, (phase, "agents", "", "cyan"))
        _print_phase_banner(label, role, desc, color)

    # ── Handle --resume from CLI ──
    resume_session: Session | None = None
    if resume_id:
        resume_session = load_session(work_dir, resume_id)
        if resume_session is None:
            console.print(f"[bold red]Session {resume_id} not found.[/]")
            return
        next_phase = resume_session.next_incomplete_phase() or "done"
        console.print(
            f"[bold cyan]Resuming session [yellow]{resume_session.session_id}[/yellow] "
            f"from phase: {next_phase}[/]"
        )

    # ── Main loop: keep accepting tasks until double Ctrl+C ──
    first_task = task  # from CLI argument, if any

    while True:
        try:
            # ── Determine the task for this iteration ──
            current_session: Session | None = None

            if resume_session:
                # --resume or /resume: use the saved session
                current_task = resume_session.task
                selected_tier = resume_session.tier
                current_session = resume_session
                resume_session = None  # consume — only first iteration
            elif first_task:
                current_task = first_task
                first_task = None  # only use CLI arg for the first run
            else:
                console.print()
                current_task = _prompt_task()
                _flush_stdin()  # discard leftover paste data

            # Handle /resume from prompt
            if isinstance(current_task, str) and current_task.startswith("__RESUME__"):
                sid = current_task[len("__RESUME__"):]
                loaded = load_session(work_dir, sid)
                if loaded is None:
                    console.print(f"[bold red]Session {sid} not found.[/]")
                    continue
                next_phase = loaded.next_incomplete_phase() or "done"
                console.print(
                    f"[bold cyan]Resuming session [yellow]{loaded.session_id}[/yellow] "
                    f"from phase: {next_phase}[/]"
                )
                current_task = loaded.task
                current_session = loaded
                selected_tier = loaded.tier

            # Handle /pause and /stop from prompt
            if isinstance(current_task, str) and current_task == "__PAUSE__":
                console.print("[dim]Paused. Enter a new task or Ctrl+C twice to exit.[/]")
                continue
            if isinstance(current_task, str) and current_task == "__STOP__":
                os._exit(0)

            # Handle /ask — quick admin Q&A using Claude (haiku), no task started
            if isinstance(current_task, str) and current_task.startswith("__ASK__"):
                question = current_task[len("__ASK__"):]
                _print_phase_banner(
                    "Admin Query", "Claude (haiku)",
                    "Quick Q&A — no task pipeline started", "cyan",
                )
                ask_agent = ClaudeAgent(
                    model="claude-haiku-4-5-20251001",
                    timeout=60,
                    working_dir=work_dir,
                )
                try:
                    log_info("Claude (haiku) is answering your question ...")
                    answer = ask_agent.query(question)
                    console.print()
                    console.print(Panel(
                        answer,
                        title="[bold cyan]Admin Response[/]",
                        border_style="cyan",
                        padding=(1, 2),
                    ))
                except Exception as exc:
                    log_error(f"Admin query failed: {exc}")
                continue

            # Handle /init — bootstrap all memory files from the existing codebase
            if isinstance(current_task, str) and current_task == "__INIT__":
                init_agent = QwenAgent(
                    model=cfg["qwen_model"],
                    timeout=cfg["timeout_seconds"],
                    working_dir=work_dir,
                )
                try:
                    run_init(work_dir, init_agent)
                except Exception as exc:
                    log_error(f"Project init failed: {exc}")
                continue

            # ── Tier & mode selection (interactive, per-task) ──
            if current_session is None:
                # Normal flow — not resuming
                if tier:
                    # CLI-specified tier — apply once
                    selected_tier = tier
                    tier_cfg = cfg.get("tiers", _DEFAULT_TIERS).get(selected_tier, {})
                    for key, val in tier_cfg.items():
                        cfg[key] = val
                else:
                    # Interactive tier selection
                    selected_tier = _prompt_tier(cfg)

                # Interactive auto-mode selection (if not already set via CLI)
                if not is_auto_all() and not auto_mode:
                    if _prompt_auto_mode():
                        set_auto_all(True)
                        console.print("[bold cyan]  Auto-approve mode: ON[/]")
            else:
                # Resuming — apply tier from session
                tier_cfg = cfg.get("tiers", _DEFAULT_TIERS).get(selected_tier, {})
                for key, val in tier_cfg.items():
                    cfg[key] = val

            # Create/update agents with current config (tier may have changed dev_model)
            claude = ClaudeAgent(
                model=cfg["claude_model"],
                timeout=cfg["timeout_seconds"],
                working_dir=work_dir,
                dev_model=cfg.get("dev_model", cfg["claude_model"]),
            )
            codex = CodexAgent(
                model=cfg["codex_model"],
                timeout=cfg["codex_timeout_seconds"],
                working_dir=work_dir,
            )
            qwen = QwenAgent(
                model=cfg["qwen_model"],
                timeout=cfg["timeout_seconds"],
                working_dir=work_dir,
            )

            _run_task(current_task, cfg, work_dir, claude, codex, qwen, phase,
                      tier=selected_tier, session=current_session, dry_run=dry_run)

            # Reset auto-all after each task (unless set via CLI)
            if not auto_mode:
                set_auto_all(False)

        except Exception as exc:
            log_error(f"Orchestrator failed: {exc}")
            console.print("[dim]Enter a new task or Ctrl+C twice to exit.[/]")
            continue


if __name__ == "__main__":
    main()
