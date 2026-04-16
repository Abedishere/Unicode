#!/usr/bin/env bash
# ── Orchestrator test runner ────────────────────────────────────────────────
# Usage:
#   bash scripts/test_run.sh <task> [extra orchestrator flags...]
#
# Defaults (all overridable via flags):
#   --tier quick        (sonnet dev, 1 review round, 1 discussion round)
#   --auto              (auto-approve all gates except git commit)
#   --phase implement   (skip discuss/plan by default — fastest)
#   --working-dir       (Desktop/test-orchestrator, auto-created if absent)
#
# Examples:
#   bash scripts/test_run.sh "add a hello_world.py"
#   bash scripts/test_run.sh "add logging" --phase all
#   bash scripts/test_run.sh "add utils.py" --tier standard --working-dir /some/repo
#
# Fallback simulation:
#   ORCHESTRATOR_SIMULATE_LIMIT_AFTER=1 bash scripts/test_run.sh "add two files"
# ────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR="$(cd "$SCRIPT_DIR/.." && pwd)/orchestrator.py"

# Default test working directory — created and git-initialised if absent
DEFAULT_WORK_DIR="$HOME/Desktop/test-orchestrator"

# ── Parse first positional arg as task, rest forwarded to orchestrator ──────
if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/test_run.sh <task> [extra flags...]"
  exit 1
fi
TASK="$1"
shift

# ── Ensure test repo exists ─────────────────────────────────────────────────
if [[ ! -d "$DEFAULT_WORK_DIR/.git" ]]; then
  mkdir -p "$DEFAULT_WORK_DIR"
  cd "$DEFAULT_WORK_DIR"
  git init -q
  echo "# test-orchestrator" > README.md
  git add README.md
  git -c user.email="test@test.com" -c user.name="test" commit -qm "init"
  echo "[test_run] Created test repo at $DEFAULT_WORK_DIR"
fi

# ── Resolve working dir: honour --working-dir flag if passed ────────────────
WORK_DIR="$DEFAULT_WORK_DIR"
EXTRA_ARGS=("$@")
for i in "${!EXTRA_ARGS[@]}"; do
  if [[ "${EXTRA_ARGS[$i]}" == "--working-dir" && -n "${EXTRA_ARGS[$((i+1))]+x}" ]]; then
    WORK_DIR="${EXTRA_ARGS[$((i+1))]}"
  fi
done

# Check if --working-dir / --tier / --auto / --phase are already provided
HAS_WORKDIR=false; HAS_TIER=false; HAS_AUTO=false; HAS_PHASE=false
for arg in "$@"; do
  [[ "$arg" == "--working-dir" ]] && HAS_WORKDIR=true
  [[ "$arg" == "--tier" ]]        && HAS_TIER=true
  [[ "$arg" == "--auto" ]]        && HAS_AUTO=true
  [[ "$arg" == "--phase" ]]       && HAS_PHASE=true
done

# Build default flags
DEFAULTS=()
[[ "$HAS_WORKDIR" == false ]] && DEFAULTS+=(--working-dir "$DEFAULT_WORK_DIR")
[[ "$HAS_TIER"    == false ]] && DEFAULTS+=(--tier quick)
[[ "$HAS_AUTO"    == false ]] && DEFAULTS+=(--auto)
[[ "$HAS_PHASE"   == false ]] && DEFAULTS+=(--phase implement)

echo ""
echo "=== Orchestrator Test Run ==="
echo "Task     : $TASK"
echo "Work dir : $WORK_DIR"
[[ -n "${ORCHESTRATOR_SIMULATE_LIMIT_AFTER:-}" ]] && \
  echo "Fallback : simulating limit after ${ORCHESTRATOR_SIMULATE_LIMIT_AFTER} file(s)"
echo ""

python "$ORCHESTRATOR" \
  "${DEFAULTS[@]}" \
  "$@" \
  "$TASK"
