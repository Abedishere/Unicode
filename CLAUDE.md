# Project Context (Claude Code)

Managed by the AI Orchestrator. Claude Code reads this file on startup.

## Architecture
See `orchestrator.md` for project summary, folder structure, and component notes.

## Memory
All persistent memory lives in `.orchestrator/`:
- `bugs.md` · `decisions.md` · `key_facts.md` · `issues.md` · `memory.yaml`

Check these before making architectural changes or debugging known issues.
Run `/init` to have Kiro populate them from the codebase if they are empty.

---

## Testing the Orchestrator

### Quick test runner
```bash
cd C:/Users/PinkPanther/unicode
bash scripts/test_run.sh "<task description>" [extra flags]
```

Defaults applied automatically: `--tier quick --auto --phase implement --working-dir ~/Desktop/test-orchestrator`

Override any default by passing the flag explicitly:
```bash
bash scripts/test_run.sh "add logging" --phase all
bash scripts/test_run.sh "refactor utils" --tier standard --working-dir /path/to/repo
```

### Testing the fallback chain (usage-limit simulation)
Set `ORCHESTRATOR_SIMULATE_LIMIT_AFTER=N` to make the implementation worker raise `UsageLimitReached` after N files complete. Codex (then Kiro) picks up the rest.

```bash
ORCHESTRATOR_SIMULATE_LIMIT_AFTER=1 bash scripts/test_run.sh "create two files: a.py and b.py"
```

Expected output: `⚠ Claude limit reached — switching to Codex for N file(s)`

### Windows terminal encoding
The orchestrator reconfigures stdout/stderr to UTF-8 at startup (`orchestrator.py` lines 15–27), so it works from the Bash tool without any env var overrides.

---

## Key Architectural Changes (recent)

### Global usage-limit fallback (`utils/fallback.py`)
When any agent hits its usage/rate limit, `UsageLimitReached` propagates up and the next agent in `FALLBACK_CHAIN = ["claude", "codex", "kiro"]` takes over. Applied system-wide:
- **implement**: parallel workers catch `UsageLimitReached` → mark file `"Limit"` → fallback agent handles remaining files
- **review**: Codex limit → Kiro reviews; Claude secondary limit → gracefully accepts Codex primary
- **discuss / plan / finalize**: wrapped in try/except in `orchestrator.py`, retry with next agent

Limit detection lives in `utils/runner.py: _is_usage_limit()` + `UsageLimitReached` exception.

### Parallel Kiro memory synthesis (`phases/implement.py: _synthesize_memory_parallel`)
After implementation, 5 parallel Kiro agents update the 5 memory files simultaneously (replacing the old sequential per-file loop). Each agent:
- Gets a snapshot of all current memory files as context ("skeleton")
- Specialises in one file: `bugs.md`, `decisions.md`, `key_facts.md`, `issues.md`, `memory.yaml`
- Writes only to its own file (no conflicts)

Agents: `_MEMORY_AGENT_CONFIGS` list + `_synthesize_memory_parallel()` in `phases/implement.py`.
