# Project Context (Claude Code)

Managed by the AI Orchestrator. Claude Code reads this file on startup.

## Architecture
See `orchestrator.md` for project summary, folder structure, and component notes.

## Memory
All persistent memory lives in `.orchestrator/`:
- `bugs.md` · `decisions.md` · `key_facts.md` · `issues.md` · `memory.yaml`

Check these before making architectural changes or debugging known issues.
Run `/init` to have Qwen populate them from the codebase if they are empty.
