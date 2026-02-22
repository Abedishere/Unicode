"""Session persistence for the AI orchestrator.

Each session is a JSON file in ``.orchestrator/sessions/`` that tracks:
- Task description and configuration
- Which phases have completed and their outputs
- Timestamps and status

Sessions can be saved / loaded / listed / resumed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


_SESSIONS_DIR = ".orchestrator/sessions"

# Ordered phase names — used for resume logic.
PHASE_ORDER = ("plan", "discussion", "implement", "review", "finalize")


class Session:
    """Represents a single orchestrator session with phase checkpoints."""

    def __init__(
        self,
        session_id: str | None = None,
        task: str = "",
        tier: str = "standard",
        cfg: dict | None = None,
    ):
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.task = task
        self.tier = tier
        self.cfg = cfg or {}
        self.status: str = "created"  # created | running | paused | completed | failed
        self.created_at: str = datetime.now().isoformat()
        self.updated_at: str = self.created_at

        # Phase outputs — ``None`` means not started / not yet completed.
        self.phases: dict[str, Any] = {p: None for p in PHASE_ORDER}

        # Which phase we were on when paused / interrupted.
        self.current_phase: str | None = None

    # ── helpers ──────────────────────────────────────────────────

    def mark_phase_done(self, phase: str, result: Any) -> None:
        """Record that *phase* completed with *result*."""
        self.phases[phase] = result
        self.updated_at = datetime.now().isoformat()

    def next_incomplete_phase(self) -> str | None:
        """Return the first phase that has not completed, or *None*."""
        for phase in PHASE_ORDER:
            if self.phases[phase] is None:
                return phase
        return None

    def phase_done(self, phase: str) -> bool:
        """Check whether *phase* has a stored result."""
        return self.phases.get(phase) is not None

    # ── serialisation ────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "task": self.task,
            "tier": self.tier,
            "cfg": self.cfg,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "phases": self.phases,
            "current_phase": self.current_phase,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        s = cls.__new__(cls)
        s.session_id = data["session_id"]
        s.task = data.get("task", "")
        s.tier = data.get("tier", "standard")
        s.cfg = data.get("cfg", {})
        s.status = data.get("status", "unknown")
        s.created_at = data.get("created_at", "")
        s.updated_at = data.get("updated_at", "")
        s.phases = data.get("phases", {p: None for p in PHASE_ORDER})
        s.current_phase = data.get("current_phase")
        return s


# ── CRUD operations ──────────────────────────────────────────────


def _sessions_dir(working_dir: str) -> Path:
    p = Path(working_dir) / _SESSIONS_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_session(working_dir: str, session: Session) -> Path:
    """Persist a session to disk as JSON."""
    session.updated_at = datetime.now().isoformat()
    path = _sessions_dir(working_dir) / f"{session.session_id}.json"
    path.write_text(
        json.dumps(session.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_session(working_dir: str, session_id: str) -> Session | None:
    """Load a session by *session_id*.  Returns ``None`` if not found."""
    path = _sessions_dir(working_dir) / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session.from_dict(data)
    except Exception:
        return None


def list_sessions(working_dir: str) -> list[Session]:
    """Return all saved sessions, newest first."""
    sdir = _sessions_dir(working_dir)
    sessions: list[Session] = []
    for p in sorted(
        sdir.glob("*.json"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    ):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            sessions.append(Session.from_dict(data))
        except Exception:
            continue
    return sessions


def delete_session(working_dir: str, session_id: str) -> bool:
    """Delete a session file.  Returns ``True`` if deleted."""
    path = _sessions_dir(working_dir) / f"{session_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False
