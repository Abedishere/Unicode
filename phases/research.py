"""Phase 0.5: Research — 2 Codex + 1 Qwen in parallel, synthesizer distills.

Workflow:
  1. Codex-A  — researches similar products, open-source projects, and libraries.
  2. Codex-B  — researches technical implementation approaches and pitfalls.
  3. Qwen     — researches architectural patterns via DuckDuckGo MCP (unlimited, no key).
  4. Synthesizer (Haiku by default) — reads all three, distills every key finding
               into a single compact brief that is prepended to the task prompt.
               It summarizes — it does not choose, recommend, or advise.
"""

from __future__ import annotations

import concurrent.futures

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agents.base import BaseAgent
from agents.qwen_agent import QwenAgent
from utils.logger import log_agent, log_info, log_phase

console = Console()


# ── Per-agent research prompts ────────────────────────────────────────────────

_CODEX_A_PROMPT = """\
<role>You are a senior product researcher. Study the task and share what you know.</role>

<task>{task}</task>

<rules>
Look for:
- Existing apps, SaaS tools, or open-source projects that address this problem
- Libraries or APIs that are commonly used for this kind of feature
- What the leading solutions do well and where their gaps are

Write informal notes — bullets are fine. No preamble. Just the facts.
</rules>
"""

_CODEX_B_PROMPT = """\
<role>You are a senior technical researcher. Study the task and share technical findings.</role>

<task>{task}</task>

<rules>
Look for:
- Common implementation approaches and their trade-offs
- Packages, SDKs, or protocols that are well-suited to this task
- Edge cases, gotchas, or known bugs teams run into
- Relevant specs, RFCs, or community conventions

Write informal technical notes — bullets are fine. No preamble. Just the facts.
</rules>
"""

_QWEN_PROMPT = """\
<role>You are a senior architect and code-quality researcher. You have a web search tool.</role>

<task>{task}</task>

<rules>
STRICT RULES — follow exactly:
1. Call the search tool ONCE with a single focused query.
2. Do NOT call fetch_content or visit any URLs.
3. Do NOT search again after the first result.
4. Work only with the snippets returned by the search.

Your ONE search query should find: architectural patterns, common mistakes, \
and security/performance considerations for the task above.

After the single search, write your findings as informal bullet notes. \
No preamble, no URLs, no conclusion. Just the facts from the snippets.
</rules>
"""

_SYNTHESIS_PROMPT = """\
<role>You are a technical researcher aggregating notes from three independent engineers.</role>

<role>
YOUR ONLY JOB is to distill their findings into a single, compact summary that \
captures every key piece of information across all three sets of notes. \
Preserve all important details. Do not drop anything meaningful. \
If findings overlap, merge them into one bullet. If they conflict or differ, \
keep both perspectives and note the difference. Do not advise, recommend, or \
tell anyone what to do — just present what was found.

The output will be injected as background context for a planning session. \
It should read like a factual briefing, not a set of instructions.
</role>

<task>{task}</task>

<context>
━━━ CODEX (products & libraries) ━━━
{codex_a}

━━━ CODEX (technical patterns) ━━━
{codex_b}

━━━ QWEN (architecture, web-searched) ━━━
{qwen}
</context>

<output_format>
Keep it under 400 words. Use bullets. Use only the headings that have content:

## Existing Solutions & Libraries
## Technical Approaches & Trade-offs
## Architectural Patterns
## Known Pitfalls & Challenges
## Conflicting Findings  ← only if engineers disagreed on something
</output_format>

Write the aggregated briefing now. Start directly with the first heading. \
No preamble, no conclusion, no advice.
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _query(agent: BaseAgent, prompt: str, *, web_search: bool = False) -> str:
    try:
        if web_search and isinstance(agent, QwenAgent):
            result = agent.research_query(prompt)
        else:
            result = agent.query(prompt)
        return result.strip() if result else ""
    except Exception as exc:  # noqa: BLE001
        log_info(f"{agent.name} research failed (non-fatal): {exc}")
        return ""


# ── Public entry point ────────────────────────────────────────────────────────

def run_research(
    task: str,
    codex: BaseAgent,
    qwen: BaseAgent,
    synthesizer: BaseAgent,
    wall_seconds: int = 90,
) -> str:
    """Run parallel research and return the task prompt enriched with findings.

    Agents:
      - Codex ×2 in parallel (two different research angles)
      - Qwen   ×1 in parallel (web search via DuckDuckGo MCP, no API key)
      - synthesizer ×1 sequential (distills all findings — Haiku recommended)

    wall_seconds: hard deadline for the parallel phase. Agents that finish
    within the window contribute; any that are still running are skipped so
    the pipeline doesn't stall. Defaults to 90s.

    Returns the enriched task string (original task + research context).
    Returns the original task unchanged if all agents fail or find nothing.
    """
    log_phase("Phase 0.5: Research")
    console.print(
        f"[dim]Codex (×2) and Qwen are researching in parallel "
        f"(max {wall_seconds}s) …[/]"
    )
    console.print()

    codex_a_prompt = _CODEX_A_PROMPT.format(task=task)
    codex_b_prompt = _CODEX_B_PROMPT.format(task=task)
    qwen_prompt    = _QWEN_PROMPT.format(task=task)

    # ── 1. Parallel research with hard wall-clock deadline ────────────────────
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)
    try:
        f_a = pool.submit(_query, codex, codex_a_prompt)
        f_b = pool.submit(_query, codex, codex_b_prompt)
        f_q = pool.submit(_query, qwen,  qwen_prompt, web_search=True)

        done, pending = concurrent.futures.wait(
            [f_a, f_b, f_q], timeout=wall_seconds
        )
        if pending:
            log_info(
                f"{len(pending)} research agent(s) hit the {wall_seconds}s wall "
                "— using partial results."
            )
            for fut in pending:
                fut.cancel()

        def _get(fut: concurrent.futures.Future) -> str:
            if fut not in done:
                return ""
            try:
                return fut.result() or ""
            except Exception:
                return ""

        codex_a = _get(f_a)
        codex_b = _get(f_b)
        qwen_r  = _get(f_q)
    finally:
        pool.shutdown(wait=False)

    log_agent("Codex (products & libraries)", codex_a or "(no findings)")
    log_agent("Codex (technical patterns)",   codex_b or "(no findings)")
    log_agent("Qwen  (architecture + web)",   qwen_r  or "(no findings)")

    if not any([codex_a, codex_b, qwen_r]):
        log_info("All research agents returned empty — skipping synthesis.")
        return task

    # ── 2. Synthesis (Haiku distills — summarizes, does not advise) ───────────
    log_info(f"Synthesizing with {synthesizer.name} …")
    brief = _query(synthesizer, _SYNTHESIS_PROMPT.format(
        task=task,
        codex_a=codex_a or "(no findings)",
        codex_b=codex_b or "(no findings)",
        qwen=qwen_r     or "(no findings)",
    ))

    if not brief:
        log_info("Synthesis returned empty — skipping.")
        return task

    console.print(Panel(
        Text(brief),
        title=f"[bold cyan]Research Brief ({synthesizer.name} synthesis)[/]",
        border_style="cyan",
    ))
    log_info("Research complete — brief prepended to task prompt.")

    # ── 3. Return enriched task ───────────────────────────────────────────────
    return (
        f"{task}\n\n"
        f"━━━ BACKGROUND RESEARCH (context only) ━━━\n"
        f"{brief}"
    )
