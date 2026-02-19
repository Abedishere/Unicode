# Project Context (Codex)

Managed by the AI Orchestrator. Codex CLI reads this file on startup.

## Project Architecture
See `orchestrator.md` in this directory for a full project summary, folder structure, architecture overview, and notes on what each component does.

- Latest task: make drag-and-drop image uploads auto-attach to the top image menu (same behavior as `/image`) instead of becoming pasted text.
- Outcome: implementation direction agreed; use a minimal, targeted change in `orchestrator.py` with tests. `/image` refactor is explicitly deferred.

- Key decisions:
- Reuse existing primitives: `_clean_path`, `_is_image_path`, `_try_attach_image`.
- Add one helper for pasted payload inspection (e.g. `_paste_is_image_path` / `_paste_is_image`) that returns a cleaned image path or `None`.
- Helper must be conservative: auto-attach only when the full non-blank paste payload resolves to one valid image path; otherwise preserve existing text paste behavior.
- Handle drag-drop artifacts (multi-line/wrapped payloads, trailing blank lines) safely; do not rely on a single naive join strategy.

- Required insertion points:
- Primary paste branch around `orchestrator.py:899` (before text attachment logic).
- Continuation paste branch around `orchestrator.py:941` (before continuation text-paste handling).
- In both places: if helper returns path, call `_try_attach_image(...)` and `continue` to avoid creating a text attachment.

- UI/control-flow conventions:
- Mirror existing single-line image flow at `orchestrator.py:894-896`.
- In primary branch, redraw handling should follow the existing image attach pattern (`_erase_screen_from(saved=True)` + `continue`).
- In continuation branch, do not add `_erase_screen_from`; match existing continuation image handling behavior and just `continue`.

- Out of scope (explicitly deferred):
- Unifying `/image` command internals with `_try_attach_image`.
- UX/error-message unification between explicit `/image` failures and silent auto-detect path.

- Validation expectations:
- Add tests for:
- single image path with trailing blank lines -> auto-attaches.
- wrapped drag-drop payload that still represents one image path -> auto-attaches.
- normal multiline pasted text containing image-like tokens -> remains text paste (no auto-attach).
