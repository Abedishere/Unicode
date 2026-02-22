# Project Context (Claude Code)

Managed by the AI Orchestrator. Claude Code reads this file on startup.

## Project Architecture
See `orchestrator.md` in this directory for a full project summary, folder structure, architecture overview, and notes on what each component does.

## Project Memory System

This project maintains institutional knowledge in `docs/project_notes/` for consistency across sessions.

### Memory Files

- **bugs.md** — Bug log with dates, root causes, solutions, and prevention notes
- **decisions.md** — Architectural Decision Records (ADRs) with context and trade-offs
- **key_facts.md** — Project configuration, credentials, ports, important URLs
- **issues.md** — Work log with task descriptions and outcomes

### Memory-Aware Protocols

**Before proposing architectural changes:**
- Check `docs/project_notes/decisions.md` for existing decisions
- Verify the proposed approach doesn't conflict with past choices
- If it does conflict, acknowledge the existing decision and explain why a change is warranted

**When encountering errors or bugs:**
- Search `docs/project_notes/bugs.md` for similar issues
- Apply known solutions if found
- Document new bugs and their solutions when resolved

**When looking up project configuration:**
- Check `docs/project_notes/key_facts.md` for credentials, ports, URLs, service accounts
- Prefer documented facts over assumptions

**When completing work:**
- Outcomes are logged automatically by the orchestrator in `docs/project_notes/issues.md`

## Skills Ecosystem

This project uses the `npx skills` ecosystem. Skills are available in `.claude/skills/`.

### find-skills
Enables agents to search for and install new skills from the registry when specialized knowledge is needed.
- **Usage:** `npx skills find [query]` — search the registry; `npx skills add <url> --skill <name>` — install
- **When to use:** When you encounter a task that might benefit from a specialized skill (e.g., testing frameworks, language-specific patterns, deployment workflows)

### project-memory
Defines the structured format for `docs/project_notes/` entries.
- **Bug entries:** `### YYYY-MM-DD - <title>` with Issue / Root Cause / Solution / Prevention fields
- **ADRs:** `### ADR-NNN: <title> (date)` with Context / Decision / Alternatives / Consequences fields
- **Key facts:** `### <Category>` with `- [date] <fact>` bullets
- **Work log:** `### YYYY-MM-DD - <task>` with Status / URL / Notes fields
- The orchestrator writes to these files automatically via `utils/memory.py`
