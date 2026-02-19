# Project Context (Claude Code)

Managed by the AI Orchestrator. Claude Code reads this file on startup.

## Project Architecture
See `orchestrator.md` in this directory for a full project summary, folder structure, architecture overview, and notes on what each component does.

## Key Patterns & Conventions

### Image Pipeline
`_clean_path(s)` → `_is_image_path(s)` → `_try_attach_image(s)`.
Attachments stored in `_attached_images: list[str]` and `_attached_pastes: list[str]`.

### Input Loop (`_prompt_line_raw`)
- Windows-only raw input via `msvcrt.getwch()`.
- `primary=True` enables slash-command menu and ↑-to-select attachment mode.
- Prompt is `"> "` (2 chars). ANSI column = `cursor + 3` (1-based).
- Slash menu rendered below cursor with relative movement (`\033[{n}A` to return). **Do not use SCO save/restore** (`\033[s`/`\033[u`) — it breaks when the terminal scrolls near the bottom.

### `_erase_screen_from` Behavior
- Primary loop: erases from the DEC-saved cursor position downward.
- Continuation loop: does not erase — avoids wiping the multiline text above.

### Auto-Attach on Paste/Drag
- Single image path pasted at prompt → auto-attached by `_try_attach_image` (line ~905).
- Multi-line drag-drop → handled by `_paste_is_image_path` (line ~912).
