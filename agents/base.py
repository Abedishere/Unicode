from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """Abstract base class for AI agent wrappers."""

    def __init__(self, model: str, timeout: int, working_dir: str):
        self.model = model
        self.timeout = timeout
        self.working_dir = working_dir

    @property
    @abstractmethod
    def name(self) -> str:
        """Display name for this agent."""

    @abstractmethod
    def query(self, prompt: str) -> str:
        """Send a prompt and return the agent's text response."""
