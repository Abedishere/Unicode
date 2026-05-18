#!/usr/bin/env python3
"""Claude Code Stop hook — flush any pending global patterns to the global store.

Reads .orchestrator/global_patterns_pending.yaml (written during orchestrator
runs) and merges into ~/.unicode/global/global_patterns.yaml.
"""
import json
import sys
from pathlib import Path

try:
    data = json.loads(sys.stdin.read() or "{}")
except Exception:
    data = {}

cwd = data.get("cwd", "")
if not cwd:
    sys.exit(0)

pending = Path(cwd) / ".orchestrator" / "global_patterns_pending.yaml"
if not pending.exists():
    sys.exit(0)

try:
    sys.path.insert(0, str(Path(cwd)))
    import yaml
    from utils.global_memory import write_global_patterns

    entries = yaml.safe_load(pending.read_text(encoding="utf-8")) or []
    if entries:
        write_global_patterns(entries, source_project=Path(cwd).name)
    pending.unlink()
except Exception:
    pass  # never block a session stop over memory housekeeping

sys.exit(0)
