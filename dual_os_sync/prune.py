"""Prune short sessions — delete sessions with too few user prompts."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from .config import AppConfig, TaskConfig

logger = logging.getLogger(__name__)


def run_prune(
    config: AppConfig,
    tasks: list[TaskConfig],
    *,
    min_user_prompts: int = 1,
    dry_run: bool = False,
) -> list[str]:
    """Delete sessions with fewer than *min_user_prompts* user messages.

    Operates on **both** sides (Windows mount + Linux native) so the
    cleanup is effective regardless of which OS you run on.

    Returns human-readable report lines.
    """
    reports: list[str] = []
    total_deleted = 0
    total_remaining = 0
    is_linux = sys.platform == "linux"

    for task in tasks:
        # -- resolve paths --------------------------------------------------
        if is_linux:
            linux_dir = Path(task.linux_path).expanduser().resolve()
            win_dir = config.access.resolve_win_path_on_linux(task.win_path)
        else:
            win_dir = Path(task.win_path).expanduser().resolve()
            linux_resolved = config.access.resolve_linux_path_on_windows(
                task.linux_path
            )
            if linux_resolved is None:
                reports.append(
                    f"[SKIP] {task.name} — "
                    "win_mount_linux_root not configured, cannot reach Linux side"
                )
                continue
            linux_dir = linux_resolved

        win_dir = win_dir.resolve()
        linux_dir = linux_dir.resolve()

        # -- collect session ids from both sides ---------------------------
        session_ids: set[str] = set()
        for d in (win_dir, linux_dir):
            if d.is_dir():
                for f in d.iterdir():
                    if f.suffix == ".jsonl" and f.is_file():
                        session_ids.add(f.stem)

        if not session_ids:
            continue

        task_deleted = 0
        task_remaining = 0

        for sid in sorted(session_ids):
            # Read from whichever side has the file
            local_path = linux_dir / f"{sid}.jsonl"
            if not local_path.exists():
                local_path = win_dir / f"{sid}.jsonl"
            if not local_path.exists():
                continue  # ghost session – skip

            count = _count_user_prompts(local_path)

            if count <= min_user_prompts:
                if dry_run:
                    logger.info(
                        "[DRY-RUN] would delete session %s "
                        "(%d user prompt(s), threshold=%d)",
                        sid, count, min_user_prompts,
                    )
                    task_deleted += 1
                    continue

                # Delete from both sides
                _delete_session(win_dir, sid)
                _delete_session(linux_dir, sid)
                task_deleted += 1
                logger.info(
                    "Deleted session %s (%d user prompt(s), threshold=%d)",
                    sid, count, min_user_prompts,
                )
            else:
                task_remaining += 1

        total_deleted += task_deleted
        total_remaining += task_remaining

        if task_deleted or task_remaining:
            direction = "DRY-RUN " if dry_run else ""
            reports.append(
                f"[{direction}PRUNE] {task.name} — "
                f"deleted {task_deleted}, remaining {task_remaining}"
            )

    reports.append(
        f"Summary: deleted {total_deleted} session(s), "
        f"{total_remaining} remaining"
    )
    return reports


# ======================================================================
#  Internal helpers
# ======================================================================


def _count_user_prompts(path: Path) -> int:
    """Count real user messages in a JSONL session file.

    Skips infrastructure entries that Claude Code records as ``type: "user"``
    but are not actual conversation messages:

    * Built-in slash commands (e.g. ``/model``, ``/help``)
    * Local command execution tracking (``<local-command-caveat>``,
      ``<command-name>``, ``<command-message>``, ``<local-command-stdout>``)
    * System reminders and session renames (``<system-reminder>``)
    """
    _INFRA_PREFIXES = (
        "<local-command-caveat>",
        "<command-name>",
        "<command-message>",
        "<local-command-stdout>",
        "<system-reminder>",
    )

    count = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "user":
                        content = obj.get("message", {}).get("content", "")
                        if not isinstance(content, str):
                            continue
                        # Skip bare slash commands (/model, /help, etc.)
                        if content.startswith("/"):
                            continue
                        # Skip infrastructure XML blocks
                        if content.startswith(_INFRA_PREFIXES):
                            continue
                        count += 1
                except json.JSONDecodeError:
                    continue
    except OSError:
        logger.warning("Cannot read %s — skipping", path)
        return 0
    return count


def _delete_session(projects_dir: Path, session_id: str) -> None:
    """Remove a session's ``.jsonl`` file and its ``{session_id}/`` directory."""
    jsonl = projects_dir / f"{session_id}.jsonl"
    if jsonl.exists():
        jsonl.unlink()
    subdir = projects_dir / session_id
    if subdir.is_dir():
        import shutil
        shutil.rmtree(subdir)
