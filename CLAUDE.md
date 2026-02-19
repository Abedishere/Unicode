# Project Context (Claude Code)

Managed by the AI Orchestrator. Claude Code reads this file on startup.

## Project Architecture
See `orchestrator.md` in this directory for a full project summary, folder structure, architecture overview, and notes on what each component does.

It looks like I need write permission to `CLAUDE.md`. Could you approve the edit? The content I'm writing includes:

- **Recent Task** section documenting the drag-drop auto-attach feature (problem, solution, insertion points, design decisions)
- **Key Patterns & Conventions** section covering the image pipeline (`_clean_path` → `_is_image_path` → `_try_attach_image`), attachment lists, input loop structure, and the `_erase_screen_from` behavior difference between primary and continuation loops

This gives future sessions the context needed to pick up where we left off without re-reading the full orchestrator.
