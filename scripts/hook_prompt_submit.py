#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook — inject global memory summary into session.

Reads ~/.unicode/global/global_patterns.yaml and outputs a brief summary
so Claude is aware of cross-project patterns in every session.
"""
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(json.dumps({"continue": True}))
    sys.exit(0)

global_yaml = Path.home() / ".unicode" / "global" / "global_patterns.yaml"
if not global_yaml.exists():
    print(json.dumps({"continue": True}))
    sys.exit(0)

try:
    patterns = yaml.safe_load(global_yaml.read_text(encoding="utf-8")) or []
except Exception:
    print(json.dumps({"continue": True}))
    sys.exit(0)

if not patterns:
    print(json.dumps({"continue": True}))
    sys.exit(0)

cats: dict[str, int] = {}
for p in patterns:
    cat = p.get("category", "general")
    cats[cat] = cats.get(cat, 0) + 1

lines = [f"  {cat}: {n}" for cat, n in sorted(cats.items())]
injection = f"[unicode global memory: {len(patterns)} cross-project patterns available]\n" + "\n".join(lines)

print(json.dumps({"continue": True, "context": injection}))
sys.exit(0)
