from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BaseAgent(ABC):
    """Abstract base class for AI agent wrappers."""

    def __init__(self, model: str | None, timeout: int, working_dir: str):
        self.model = model
        self.timeout = timeout
        self.working_dir = working_dir
        self._audit_dir: Path | None = None
        self._run_id: str | None = None
        self._prompt_counter: int = 0
        self._current_phase: str = "unknown"

    def enable_audit(self, work_dir: str, run_id: str) -> None:
        """Enable prompt audit logging to .orchestrator/prompts/."""
        self._audit_dir = Path(work_dir) / ".orchestrator" / "prompts"
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id

    def set_phase(self, phase: str) -> None:
        """Track the current pipeline phase so audit filenames are meaningful."""
        self._current_phase = phase

    def _maybe_audit(self, prompt: str) -> None:
        """Write prompt to audit dir if audit is enabled. Never raises."""
        if self._audit_dir is None:
            return
        try:
            safe = self.name.replace(" ", "_").replace("/", "_")
            n = self._prompt_counter
            self._prompt_counter += 1
            (self._audit_dir / f"{self._run_id}_{self._current_phase}_{safe}_{n}.txt").write_text(
                prompt, encoding="utf-8"
            )
        except OSError:
            pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Display name for this agent."""

    @abstractmethod
    def query(self, prompt: str) -> str:
        """Send a prompt and return the agent's text response."""

    @staticmethod
    def check_cli_output(stdout: str, stderr: str, agent_name: str) -> str:
        """Validate CLI output — raise on empty stdout with stderr present."""
        if not stdout.strip() and stderr.strip():
            raise RuntimeError(f"{agent_name} CLI failed: {stderr}")
        return stdout.strip()
