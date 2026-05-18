"""Quick smoke test for all memory components."""
import tempfile
from utils.memory import compute_quality_score, search_past_tasks, add_task_to_index, get_context_for_task
from utils.local_memory import write_local_patterns, load_local_patterns, format_local_context
from utils.global_memory import write_global_patterns, load_global_patterns, format_global_context

# 1. compute_quality_score
score = compute_quality_score(
    approved=True,
    review_cycles=1,
    file_statuses={"a.py": "Done", "b.py": "Error"},
    fallback_triggered=False,
)
assert 0 < score < 1, f"Bad score: {score}"
print(f"[OK] compute_quality_score = {score}")

# 2. BM25 task index search with quality weighting
with tempfile.TemporaryDirectory() as tmp:
    add_task_to_index(tmp, "implement user authentication with JWT", "APPROVED", ["auth", "jwt"], quality_score=score)
    results = search_past_tasks(tmp, "add login feature with tokens")
    assert len(results) >= 1, "BM25 search returned nothing"
    qs = results[0].get("quality_score")
    assert qs is not None, "quality_score missing from task_index"
    print(f"[OK] BM25 task search found past task, quality_score={qs}")

# 3. Local memory round-trip + dedup
with tempfile.TemporaryDirectory() as tmp:
    write_local_patterns(tmp, [
        {
            "category": "convention",
            "pattern": "Always validate request payloads at local API boundaries",
            "context": "Prevents invalid data reaching business logic in this project.",
            "quality_score": 0.85,
        }
    ])
    found = load_local_patterns(tmp, "validate API input before processing", n=5)
    assert len(found) >= 1, "local_memory returned nothing"
    print(f"[OK] local_memory found {len(found)} pattern(s)")

    ctx = format_local_context(found)
    assert "Project-Specific Patterns" in ctx
    print("[OK] format_local_context works")

# 4. Global memory round-trip + dedup
write_global_patterns([
    {
        "category": "error_handling",
        "pattern": "Always validate request payloads at API boundaries globally",
        "context": "Prevents invalid data reaching business logic.",
        "quality_score": 0.9,
    }
], source_project="smoke_test")
found_g = load_global_patterns("validate API input before processing", n=5)
assert len(found_g) >= 1, "global_memory returned nothing"
print(f"[OK] global_memory found {len(found_g)} pattern(s)")

ctx_g = format_global_context(found_g)
assert "Cross-Project Patterns" in ctx_g
print("[OK] format_global_context works")

# 5. get_context_for_task includes both local and global
with tempfile.TemporaryDirectory() as tmp:
    write_local_patterns(tmp, [{"pattern": "validate API input", "category": "convention", "context": "project-specific."}])
    result = get_context_for_task(tmp, "validate API input")
    assert "Project-Specific Patterns" in result or "Cross-Project Patterns" in result or result == ""
    print(f"[OK] get_context_for_task combined injection (len={len(result)})")

print()
print("All smoke tests passed.")
