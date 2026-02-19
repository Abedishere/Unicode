"""AI Agent Orchestrator — coordinate Claude Code and Codex CLI."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import click
import yaml
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agents.claude_agent import ClaudeAgent
from agents.codex_agent import CodexAgent
from agents.qwen_agent import QwenAgent
from phases.discuss import run_discussion
from phases.implement import run_implementation
from phases.plan import consolidate_plan
from phases.review import run_review
from utils.approval import request_approval, reset_session_approvals
from utils.git_utils import commit, push, init_repo, is_git_repo
from utils.history import append_history, agent_update_md, init_agent_md, write_orchestrator_md
from utils.logger import init_transcript, log_error, log_info, log_phase, log_success
from utils.runner import CancelledByUser, TimeoutSkipToReview


PACKAGE_DIR = Path(__file__).resolve().parent

console = Console()

VERSION = "0.1.0"

# 3-color gradient stops: orange (Claude) → teal (Codex) → purple (Qwen)
_C = "#E8915C"  # Claude orange/amber
_X = "#50C8B4"  # Codex teal/blue-green
_Q = "#7B68EE"  # Qwen purple/blue

# Box-drawing block letters (Qwen Code style)
_art_lines = [
    "▄▄    ▄▄ ▄▄▄    ▄▄ ▄▄  ▄▄▄▄▄▄   ▄▄▄▄▄▄  ▄▄▄▄▄▄  ▄▄▄▄▄▄▄",
    "██║   ██║████╗  ██║██║██╔════╝ ██╔═══██╗██╔══██╗██╔════╝",
    "██║   ██║██╔██╗ ██║██║██║      ██║   ██║██║  ██║█████╗  ",
    "██║   ██║██║╚██╗██║██║██║      ██║   ██║██║  ██║██╔══╝  ",
    "╚██████╔╝██║ ╚████║██║╚██████╗ ╚██████╔╝██████╔╝███████╗",
    " ╚═════╝ ╚═╝  ╚═══╝╚═╝ ╚═════╝  ╚═════╝ ╚═════╝ ╚══════╝",
]


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> str:
    r = int(c1[0] + (c2[0] - c1[0]) * t)
    g = int(c1[1] + (c2[1] - c1[1]) * t)
    b = int(c1[2] + (c2[2] - c1[2]) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _gradient_char(col: int, width: int) -> str:
    """Return a hex color for column position using a 3-stop gradient."""
    stops = [_hex_to_rgb(_C), _hex_to_rgb(_X), _hex_to_rgb(_Q)]
    t = col / max(width - 1, 1)  # 0.0 → 1.0
    if t <= 0.5:
        return _lerp_color(stops[0], stops[1], t / 0.5)
    else:
        return _lerp_color(stops[1], stops[2], (t - 0.5) / 0.5)


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


ASCII_ART = _build_gradient_art()


def _print_banner(cfg: dict, work_dir: str) -> None:
    """Print the startup banner with ASCII art left and info box right."""
    console.print()

    # Info panel (right side)
    info_lines = Text()
    info_lines.append(">_ ", style="bold magenta")
    info_lines.append(f"Unicode Orchestrator", style="bold white")
    info_lines.append(f" (v{VERSION})\n\n", style="dim")
    info_lines.append(f"  Claude ", style=f"bold {_C}")
    info_lines.append(f"{cfg['claude_model']}", style="dim")
    info_lines.append(f"  |  ", style="dim")
    info_lines.append(f"Codex ", style=f"bold {_X}")
    info_lines.append(f"{cfg['codex_model']}", style="dim")
    info_lines.append(f"\n  Qwen ", style=f"bold {_Q}")
    info_lines.append(f"{cfg['qwen_model']}", style="dim")
    info_lines.append(f"\n  {work_dir}", style="dim")

    info_panel = Panel(
        info_lines,
        border_style="bright_black",
        padding=(0, 1),
    )

    console.print(
        Columns([ASCII_ART, info_panel], padding=(0, 2), expand=False),
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


def _sigint_handler(signum, frame):
    """Handle Ctrl+C: first press does nothing, second press within 2s exits."""
    global _last_ctrl_c
    now = time.time()
    if now - _last_ctrl_c <= _DOUBLE_PRESS_WINDOW:
        console.print("\n[bold red]Ctrl+C ×2 — exiting unicode.[/]")
        os._exit(0)
    _last_ctrl_c = now
    console.print("\n[bold yellow]Press Ctrl+C again within 2s to exit.[/]")


def load_config(config_path: str | None) -> dict:
    defaults = {
        "discussion_rounds": 4,
        "max_review_iterations": 3,
        "claude_model": "opus",
        "codex_model": "gpt-5.3-codex",
        "qwen_model": "qwen3-coder",
        "timeout_seconds": 600,
        "codex_timeout_seconds": 300,
        "auto_commit": False,
        "allow_user_questions": True,
        "working_directory": ".",
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


def _run_phase(label: str, fn, *args, **kwargs):
    """Run a phase function, catching ESC cancellation gracefully.

    On ESC: pauses and asks retry / skip / clarify.
    Returns the function result, or None if skipped.
    """
    while True:
        try:
            return fn(*args, **kwargs)
        except CancelledByUser:
            console.print()
            console.print(f"[bold yellow]Paused:[/] {label}")
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
    """Erase all terminal content from the DEC-saved cursor position onward.

    If *saved* is True, uses DEC restore-cursor (\\0338) first. Then clears
    from cursor to end of screen. Works on Windows Terminal, iTerm2, etc.
    """
    if saved:
        sys.stdout.write("\0338")  # DEC restore cursor (DECRC)
    sys.stdout.write("\033[J")     # Erase from cursor to end of screen
    sys.stdout.flush()


# ── Slash command definitions ──────────────────────────────────────────
_SLASH_COMMANDS = [
    ("/image <path>", "Attach an image file"),
    ("/clear", "Remove all attachments"),
    ("/clear-images", "Remove attached images only"),
    ("/clear-paste", "Remove pasted text only"),
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


def _render_slash_menu(typed: str, prev_n: int, restore_col: int = 0) -> int:
    """Show the slash-command picker below the prompt line.

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
    for cmd, desc in matches:
        sys.stdout.write(f"\r\n  \033[1;35m{cmd:<22}\033[0m \033[2m{desc}\033[0m")
        rows_down += 1

    # ── Return to prompt line ────────────────────────────────────
    sys.stdout.write(f"\033[{rows_down}A")
    if restore_col > 0:
        sys.stdout.write(f"\033[{restore_col}G")
    sys.stdout.flush()
    return rows_down                          # > 0 signals "menu visible"


# ── Attachment selection-mode rendering ────────────────────────────────

def _attachment_count() -> int:
    """Total number of pending attachments (pastes + images)."""
    return len(_attached_pastes) + len(_attached_images)


def _redraw_prompt_area(selected: int = -1) -> None:
    """Erase & redraw the full prompt area from the DEC-saved position.

    Parameters
    ----------
    selected : int
        Index into the combined attachment list to highlight.
        -1 means no selection (normal display).
    """
    # Restore DEC cursor → clear everything below → re-save at same spot.
    sys.stdout.write("\0338\033[J\0337")
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


def _prompt_line_raw(prompt_ansi: str, primary: bool = False):
    """Read one line using raw keypresses (Windows ``msvcrt``).

    Supports full cursor movement (←/→, Home, End, Delete) so the prompt
    feels like a normal shell.  When *primary* is True the slash-command
    menu and ↑-to-select attachment mode are enabled.

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
    sys.stdout.flush()

    buf: list[str] = []
    cursor = 0                # position inside buf
    menu_n = 0                # slash-menu lines currently displayed

    while True:
        ch = msvcrt.getwch()

        # ── Enter ─────────────────────────────────────────────────
        if ch in ("\r", "\n"):
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
                        menu_n = _render_slash_menu(text, menu_n, col)
                    elif menu_n:
                        _clear_below_cursor(menu_n, col)
                        menu_n = 0
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
                if primary and not buf and _attachment_count() > 0:
                    if menu_n:
                        _clear_below_cursor(menu_n, 3)
                        menu_n = 0
                    _run_selection_mode()
                    # selection mode redraws with `> ` — keep reading
                # else: no-op (no history implemented)

            elif ch2 == "P":        # ↓ Down
                pass                # no-op

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
                    if len(hits) == 1:
                        comp = hits[0][len(base):]
                        if comp:
                            extra = comp + " "
                            for c in extra:
                                buf.insert(cursor, c)
                                cursor += 1
                            sys.stdout.write(extra)
                            sys.stdout.flush()
                            menu_n = _render_slash_menu("".join(buf), menu_n, cursor + 3)
            continue

        # ── Escape ────────────────────────────────────────────────
        if ch == "\x1b":
            if menu_n:
                _clear_below_cursor(menu_n, cursor + 3)
                menu_n = 0
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
            time.sleep(0.025)           # let the burst fully arrive
            got_enter = False
            while msvcrt.kbhit():
                c = msvcrt.getwch()
                if c in ("\r", "\n"):
                    got_enter = True    # Enter came with the paste
                elif ord(c) >= 32:
                    buf.insert(cursor, c)
                    cursor += 1
            # Repaint the whole prompt line cleanly
            sys.stdout.write(f"\r{prompt_ansi}{''.join(buf)}\033[K")
            sys.stdout.flush()
            candidate = "".join(buf)
            # Auto-submit if the pasted content is an image path
            if _is_image_path(candidate):
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
                return (candidate, None, "submit")

        if primary:
            text = "".join(buf)
            col = cursor + 3
            if text.startswith("/"):
                menu_n = _render_slash_menu(text, menu_n, col)
            elif menu_n:
                _clear_below_cursor(menu_n, col)
                menu_n = 0


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

    while True:
        lines: list[str] = []

        # ── Save cursor before the entire prompt area ────────────
        # (used by selection mode / paste erase to redraw cleanly)
        sys.stdout.write("\0337")  # DEC save cursor (DECSC)
        sys.stdout.flush()

        # Dim horizontal rule above prompt
        console.rule(style="bright_black")

        # Show pending attachments with hint
        has_attachments = _attachment_count() > 0
        idx = 0
        total_att = _attachment_count()
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
                )
            else:
                text, paste, action = _prompt_line_fallback(
                    "[bold magenta]> [/]",
                )

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

            # ── Auto-detect image path ────────────────────────────
            # Pure heuristic: looks like a path + has image extension.
            # Works for typed text AND single-line pastes.
            if _try_attach_image(stripped):
                _erase_screen_from(saved=True)
                continue

            # ── Paste detected ────────────────────────────────────
            if paste:
                # Multi-line drag-drop image path (e.g. & 'C:\...\img.png'\n)
                if _paste_is_image_path(paste):
                    _erase_screen_from(saved=True)
                    continue
                # Single-line paste that is an image path was already
                # caught above.  Multi-line or non-image → attach as text.
                _paste_counter += 1
                _attached_pastes.append((_paste_counter, paste))
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


def _run_task(
    task: str,
    cfg: dict,
    work_dir: str,
    claude: ClaudeAgent,
    codex: CodexAgent,
    qwen: QwenAgent,
    phase: str = "all",
) -> None:
    """Execute one full orchestration run for the given task."""
    # Reset per-session approvals for each new task
    reset_session_approvals()

    transcript_path = init_transcript(work_dir)
    log_info(f"Transcript: {transcript_path}")
    log_info(f"Task: {task}")
    start_time = time.time()

    # Track state across phases
    discussion: list[dict[str, str]] = []
    plan = ""
    approved = False
    skip_to_review = False

    # For standalone implement/review, load saved plan
    if phase in ("implement", "review"):
        plan = _load_saved_plan(work_dir)

    # ── Phase 1: Plan (Codex drafts, Claude reviews) ──
    run_plan = phase in ("all", "plan", "discuss")
    if run_plan and phase == "all":
        _print_phase_banner("Planning", "admins", "Claude & Codex will draft the plan", "cyan")
    if run_plan:
        result, extra = request_approval("plan",
            "Codex (GPT) will draft a plan, then Claude will review it.")
        if result == "proceed":
            if extra:
                task = f"{task}\n\nADDITIONAL USER INSTRUCTIONS:\n{extra}"
                log_info("Updated task with your instructions.")
            plan_result = _run_phase("Plan",
                consolidate_plan, task, claude, codex, work_dir)
            if plan_result is not None:
                plan, agreed = plan_result
            else:
                plan, agreed = "", True
        else:
            log_info("Skipping plan phase.")
            agreed = True
    else:
        agreed = True

    # ── Phase 2: Discussion (only if admins disagree on the plan) ──
    run_discuss = phase in ("all", "discuss")
    if run_discuss and not agreed and phase == "all":
        _print_phase_banner("Discussion", "admins", "Claude & Codex will discuss the plan", "cyan")
    if run_discuss and not agreed:
        log_info("Admins disagree — starting discussion to resolve.")
        result, extra = request_approval("discussion",
            "Claude and Codex disagree on the plan. They'll discuss for "
            "2 rounds to resolve.")
        if result == "proceed":
            if extra:
                task = f"{task}\n\nADDITIONAL USER INSTRUCTIONS:\n{extra}"
                log_info("Updated task with your instructions.")
            disc = _run_phase("Discussion",
                run_discussion, task, plan, claude, codex, 2,
                allow_user_questions=cfg.get("allow_user_questions", True))
            if disc is not None:
                discussion = disc

            # Re-plan after discussion
            log_info("Re-planning after discussion ...")
            plan_result = _run_phase("Re-Plan",
                consolidate_plan, task, claude, codex, work_dir, discussion)
            if plan_result is not None:
                plan, _ = plan_result
        else:
            log_info("Skipping discussion — proceeding with current plan.")
    elif not agreed:
        log_info("Admins agree — skipping discussion.")
    else:
        log_info("Admins agree — skipping discussion.")

    # Stop here if the user only wanted plan or discuss
    if phase in ("plan", "discuss"):
        log_phase("Phase complete.")
        duration = time.time() - start_time
        mins, secs = divmod(int(duration), 60)
        log_info(f"Finished in {mins}m {secs:02d}s.")
        return

    # ── Phase 3: Implementation (Claude as developer, with Qwen available) ──
    run_impl = phase in ("all", "implement")
    if run_impl and phase == "all":
        _print_phase_banner("Implementation", "developer", "Claude Code will implement the plan", "magenta")
    if run_impl:
        result, extra = request_approval("implement",
            "Claude (developer) will now implement the plan with full file access.\n"
            "Qwen is available for delegation on simple tasks.")
        if result == "proceed":
            if extra:
                plan = f"{plan}\n\nADDITIONAL USER INSTRUCTIONS:\n{extra}"
                log_info("Updated plan with your instructions.")
            try:
                impl = _run_phase("Implementation",
                    run_implementation, task, plan, claude)
                if impl is not None:
                    # Qwen writes orchestrator.md (project summary)
                    _run_phase("Writing orchestrator.md",
                        write_orchestrator_md, work_dir, task, plan, discussion, qwen)
            except TimeoutSkipToReview:
                log_info("Skipping to review phase (user request after timeout).")
                skip_to_review = True
        else:
            log_info("Skipping implementation phase.")

    # Stop here if the user only wanted implement (and didn't skip to review)
    if phase == "implement" and not skip_to_review:
        log_phase("Implementation phase complete.")
        duration = time.time() - start_time
        mins, secs = divmod(int(duration), 60)
        log_info(f"Finished in {mins}m {secs:02d}s.")
        return

    # ── Phase 4: Code Review (Codex reviews, first is mandatory) ──
    if phase == "all":
        _print_phase_banner("Code Review", "reviewer", "Codex will review the implementation", "green")
    log_info("First code review is mandatory.")
    rev = _run_phase("Code Review",
        run_review, task, plan, claude, codex, work_dir,
        cfg["max_review_iterations"])
    approved = rev if rev is not None else True

    # ── Phase 5: Finalization ──
    log_phase("Phase 5: Finalization")
    outcome = "APPROVED" if approved else "NOT APPROVED"

    if approved:
        log_success("Implementation approved!")

        # Each agent updates its own MD file
        _run_phase("Claude updating CLAUDE.md",
            agent_update_md, work_dir, task, plan, discussion, claude, "CLAUDE.md")
        _run_phase("Codex updating AGENTS.md",
            agent_update_md, work_dir, task, plan, discussion, codex, "AGENTS.md")

        # Codex writes the commit message
        commit_prompt = (
            "Write a short git commit message for these changes. "
            "One line, max 72 characters. No quotes. No prefix. "
            "Just describe what was done.\n\n"
            f"TASK: {task}\n\n"
            f"PLAN:\n{plan[:1000]}\n"
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
        f"TASK: {task}\n\nPLAN:\n{plan}\n\n"
        "List files created/modified as a bullet list. One line each. No commentary."
    )
    log_info("Qwen is summarizing actions ...")
    actions_summary = _run_phase("Summary", qwen.query, summary_prompt)
    if not actions_summary:
        actions_summary = "- Summary skipped"

    transcript_name = transcript_path.name
    append_history(work_dir, task, outcome, duration, actions_summary, transcript_name)
    log_info("Appended run to .orchestrator/history.md")

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
def main(
    task: str | None,
    config_path: str,
    rounds: int | None,
    auto_commit: bool | None,
    no_questions: bool,
    working_dir: str | None,
    phase: str,
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

    work_dir = os.path.abspath(cfg["working_directory"])
    os.makedirs(work_dir, exist_ok=True)

    # Ensure git repo exists
    if not is_git_repo(work_dir):
        init_repo(work_dir)
        log_info(f"Initialized git repo in {work_dir}")

    # Initialize agent MD files (header + orchestrator.md reference)
    init_agent_md(work_dir)

    # Print the banner with info box
    _print_banner(cfg, work_dir)

    # Create agents
    claude = ClaudeAgent(
        model=cfg["claude_model"],
        timeout=cfg["timeout_seconds"],
        working_dir=work_dir,
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

    # Show phase banner immediately on startup when a specific phase is selected
    if phase != "all":
        _phase_banners = {
            "plan":      ("Planning",       "admins",   "Claude & Codex will draft the plan",      "cyan"),
            "discuss":   ("Discussion",     "admins",   "Claude & Codex will discuss the plan",    "cyan"),
            "implement": ("Implementation", "developer","Claude Code (developer) will implement",  "magenta"),
            "review":    ("Code Review",    "reviewer", "Codex will review the implementation",    "green"),
        }
        label, role, desc, color = _phase_banners.get(phase, (phase, "agents", "", "cyan"))
        _print_phase_banner(label, role, desc, color)

    # ── Main loop: keep accepting tasks until double Ctrl+C ──
    first_task = task  # from CLI argument, if any

    while True:
        try:
            if first_task:
                current_task = first_task
                first_task = None  # only use CLI arg for the first run
            else:
                console.print()
                current_task = _prompt_task()
                _flush_stdin()  # discard leftover paste data

            _run_task(current_task, cfg, work_dir, claude, codex, qwen, phase)

        except Exception as exc:
            log_error(f"Orchestrator failed: {exc}")
            console.print("[dim]Enter a new task or Ctrl+C twice to exit.[/]")
            continue


if __name__ == "__main__":
    main()
