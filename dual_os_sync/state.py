"""State tracking — records per-task sync timestamps and mtime snapshots."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class TaskState:
    """Runtime snapshot of a single task's sync state."""

    def __init__(
        self,
        task_name: str,
        last_sync_ts: Optional[float] = None,
        last_win_mtime: Optional[float] = None,
        last_linux_mtime: Optional[float] = None,
    ) -> None:
        self.task_name = task_name
        self.last_sync_ts = last_sync_ts  # epoch timestamp
        self.last_win_mtime = last_win_mtime
        self.last_linux_mtime = last_linux_mtime

    def to_dict(self) -> dict:
        return {
            "task_name": self.task_name,
            "last_sync_ts": self.last_sync_ts,
            "last_win_mtime": self.last_win_mtime,
            "last_linux_mtime": self.last_linux_mtime,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskState":
        # Backward compat: old format had source_mtime / target_mtime
        if "last_source_mtime" in data or "last_target_mtime" in data:
            return cls(
                task_name=data["task_name"],
                last_sync_ts=data.get("last_sync_ts"),
                last_win_mtime=data.get("last_source_mtime"),
                last_linux_mtime=data.get("last_target_mtime"),
            )
        return cls(
            task_name=data["task_name"],
            last_sync_ts=data.get("last_sync_ts"),
            last_win_mtime=data.get("last_win_mtime"),
            last_linux_mtime=data.get("last_linux_mtime"),
        )


class StateManager:
    """Manages the sync_state.json file."""

    def __init__(self, state_file_path: str | Path) -> None:
        self._path = Path(state_file_path).expanduser().resolve()
        self._tasks: dict[str, TaskState] = {}

    # ------------------------------------------------------------------
    #  I/O
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Read state from disk. No-op if the file does not exist."""
        if not self._path.exists():
            return
        with open(self._path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for entry in raw.get("tasks", []):
            ts = TaskState.from_dict(entry)
            self._tasks[ts.task_name] = ts

    def save(self) -> None:
        """Persist current state to disk atomically."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "tasks": [t.to_dict() for t in self._tasks.values()],
        }
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self._path)  # atomic on same filesystem

    # ------------------------------------------------------------------
    #  Access
    # ------------------------------------------------------------------

    def get(self, task_name: str) -> Optional[TaskState]:
        return self._tasks.get(task_name)

    def update(
        self,
        task_name: str,
        win_mtime: float,
        linux_mtime: float,
    ) -> None:
        """Record a successful sync."""
        self._tasks[task_name] = TaskState(
            task_name=task_name,
            last_sync_ts=datetime.now(timezone.utc).timestamp(),
            last_win_mtime=win_mtime,
            last_linux_mtime=linux_mtime,
        )
