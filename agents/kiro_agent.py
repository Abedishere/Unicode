from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from agents.base import BaseAgent
from utils.runner import run_cli


KIRO_ROLE_PROMPTS: dict[str, str] = {
    "research": (
        "You are Unicode's research architect. Return concise factual notes. "
        "Prefer read-only tools and do not modify files."
    ),
    "skills-scout": (
        "You are Unicode's skills scout. Return only the JSON shape requested "
        "by the caller. Do not modify files."
    ),
    "init": (
        "You initialize project memory for Unicode. Return exactly the requested "
        "structured content. Prefer read-only inspection."
    ),
    "review-fallback": (
        "You are Unicode's fallback primary code reviewer. Review only the "
        "provided diff context and return the requested verdict format."
    ),
    "summary": (
        "You summarize completed Unicode runs. Be concise and follow the caller's "
        "output format exactly."
    ),
    "memory": (
        "You update Unicode project memory. Return only valid JSON when requested "
        "and do not duplicate existing entries."
    ),
    "docs": (
        "You write concise Unicode project documentation from the provided task, "
        "plan, and discussion."
    ),
}


def ensure_kiro_role_agents(working_dir: str, model: str | None) -> None:
    """Create/update local Kiro custom agents used by Unicode.

    Kiro resolves local agents from ``.kiro/agents`` under the current project.
    These configs keep role-specific behavior deterministic while allowing the
    user-selected model to be applied once.
    """
    agents_dir = Path(working_dir) / ".kiro" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for role, prompt in KIRO_ROLE_PROMPTS.items():
        data = {
            "name": f"unicode-{role}",
            "description": f"Unicode orchestrator {role} role",
            "prompt": prompt,
            "allowedTools": ["read"],
        }
        if model:
            data["model"] = model
        path = agents_dir / f"unicode-{role}.json"
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class KiroAgent(BaseAgent):
    """Wrapper around Kiro CLI non-interactive chat."""

    def __init__(
        self,
        model: str | None,
        timeout: int,
        working_dir: str,
        role: str = "memory",
    ):
        super().__init__(model=model, timeout=timeout, working_dir=working_dir)
        self.role = role

    @property
    def name(self) -> str:
        return "Kiro"

    @property
    def agent_name(self) -> str:
        return f"unicode-{self.role}"

    def _prompt_file_instruction(self, prompt: str) -> tuple[str, str]:
        out_dir = Path(self.working_dir) / ".orchestrator"
        out_dir.mkdir(parents=True, exist_ok=True)
        fd, prompt_path = tempfile.mkstemp(suffix=".txt", prefix="kiro_prompt_", dir=str(out_dir))
        os.close(fd)
        Path(prompt_path).write_text(prompt, encoding="utf-8")
        instruction = (
            f"Read the prompt at {prompt_path} and answer it exactly. "
            "Do not edit the prompt file."
        )
        return prompt_path, instruction

    def _query_with_role(self, prompt: str, role: str) -> str:
        self._maybe_audit(prompt)
        ensure_kiro_role_agents(self.working_dir, self.model)
        prompt_path, instruction = self._prompt_file_instruction(prompt)
        try:
            cmd = [
                "kiro-cli",
                "chat",
                "--no-interactive",
                "--agent",
                f"unicode-{role}",
                instruction,
            ]
            stdout, stderr = run_cli(
                cmd,
                agent_name=f"{self.name} ({role})",
                timeout=self.timeout,
                cwd=self.working_dir,
                quiet=self._quiet,
            )
            return self.check_cli_output(stdout, stderr, self.name)
        finally:
            try:
                Path(prompt_path).unlink()
            except OSError:
                pass

    def query(self, prompt: str) -> str:
        return self._query_with_role(prompt, self.role)

    def review_query(self, prompt: str) -> str:
        return self._query_with_role(prompt, "review-fallback")

    def research_query(self, prompt: str) -> str:
        return self._query_with_role(prompt, "research")
