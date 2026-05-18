# Project Context (Claude Code)

Managed by the AI Orchestrator. Claude Code reads this file on startup.

## Architecture
See `orchestrator.md` for project summary, folder structure, and component notes.

## Memory

Two stores, checked in order by every agent prompt:

**Global** (`~/.unicode/global/global_patterns.yaml`) — cross-project patterns that survive across repos. Only project-agnostic insights (no file paths, version numbers). Quality floor: 0.6.

**Local** (`.orchestrator/local_patterns.yaml`) — per-project patterns. Can include file paths, local APIs, bugs found, decisions made. Written after every run. Human-readable mirror in `.orchestrator/local_wisdom.md`.

**Task index** (`.orchestrator/memory.yaml`) — BM25-searchable log of past tasks with quality scores. Used to surface "have we done something similar before?"

Run `/init` to have Kiro populate local memory from the codebase on first use.

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

### Unified pattern-store memory (`utils/local_memory.py`, `utils/global_memory.py`)
Replaced the old 5-agent parallel markdown synthesis with a single Kiro extraction call per run. Memory now lives in two BM25Plus-indexed YAML pattern stores:

- **Local** (`utils/local_memory.py`): `.orchestrator/local_patterns.yaml` — project-specific patterns (bugs, decisions, conventions). Written by `_extract_local_patterns()` in `orchestrator.py` after every run.
- **Global** (`utils/global_memory.py`): `~/.unicode/global/global_patterns.yaml` — project-agnostic patterns shared across all repos. Written by `_extract_global_patterns()` only when `outcome == APPROVED` and `quality_score >= 0.6`.

Both use BM25Plus retrieval with quality-score weighting. Dedup by 5-consecutive-word overlap prevents redundant entries accumulating.

`get_context_for_task()` in `utils/memory.py` assembles: global patterns → local patterns → past task index, and injects the result into every agent prompt.

### Quality score (`utils/memory.py: compute_quality_score`)
Every completed task gets a 0–1 quality score derived from: review cycles, file error rate, fallback chain triggered. Stored in `task_index` and used to weight BM25 retrieval — cleaner runs surface more.

### Memory injection into research + review
- **Research phase**: top-4 global patterns prepended to the Kiro synthesizer prompt.
- **Review phase**: `past_mistakes_context` extracted from memory and prepended to the Codex reviewer prompt.

### Claude Code hooks (`scripts/hook_stop.py`, `scripts/hook_prompt_submit.py`)
- **Stop hook**: flushes any pending global patterns from `.orchestrator/global_patterns_pending.yaml` into the main global store on session end.
- **UserPromptSubmit hook**: injects a brief summary of `~/.unicode/global/global_patterns.yaml` into every Claude Code session so the AI has ambient awareness of cross-project knowledge.
