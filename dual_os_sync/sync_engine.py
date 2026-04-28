"""Sync engine — orchestrates a full sync cycle."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from .config import AppConfig, BaseMapping, TaskConfig
from .content_replacer import replace_in_json_text, replace_in_jsonl_text
from .direction import Direction, SideStatus, determine_direction, scan_side
from .state import StateManager

logger = logging.getLogger(__name__)

TMP_DIR_NAME = ".tmp_sync"


# ======================================================================
#  Public API
# ======================================================================


def run_sync(
    config: AppConfig,
    *,
    task_filter: Optional[str] = None,
    dry_run: bool = False,
    force_direction: Optional[str] = None,
    force_sync: bool = False,
) -> list[str]:
    """Execute all configured sync tasks.

    Args:
        config: Parsed application configuration.
        task_filter: If set, only run the task with this name.
        dry_run: If ``True``, report what would happen but make no changes.
        force_direction: Override sync strategy (``"win_to_linux"`` or
                         ``"linux_to_win"``).
        force_sync: If ``True``, ignore saved state and always sync.

    Returns:
        Human-readable summary lines describing what was done.
    """
    state = _init_state(config)
    tasks = _filter_tasks(config, task_filter)
    strategy = force_direction or config.settings.sync_strategy

    reports: list[str] = []

    for task in tasks:
        try:
            report = _sync_one(
                config, state, task, strategy, dry_run, force_sync
            )
            reports.append(report)
        except Exception:
            logger.exception("Sync failed for task %r", task.name)
            reports.append(f"[FAIL] {task.name} — check logs for details")

    if not dry_run:
        state.save()

    return reports


# ======================================================================
#  Single-task sync
# ======================================================================


def _sync_one(
    config: AppConfig,
    state: StateManager,
    task: TaskConfig,
    strategy: str,
    dry_run: bool,
    force_sync: bool = False,
) -> str:
    mappings = config.get_mappings_for_task(task)
    is_linux = sys.platform == "linux"

    # -- resolve actual paths -------------------------------------------------
    if is_linux:
        linux_dir = Path(task.linux_path).expanduser().resolve()
        win_dir = config.access.resolve_win_path_on_linux(task.win_path)
    else:
        win_dir = Path(task.win_path).expanduser().resolve()
        linux_resolved = config.access.resolve_linux_path_on_windows(task.linux_path)
        if linux_resolved is None:
            return (
                f"[SKIP] {task.name} — "
                "win_mount_linux_root not configured, cannot reach Linux path"
            )
        linux_dir = linux_resolved

    win_dir = win_dir.resolve()
    linux_dir = linux_dir.resolve()

    # -- scan both sides ------------------------------------------------------
    exts = task.target_extensions
    win_status = scan_side(win_dir, exts)
    linux_status = scan_side(linux_dir, exts)

    # -- determine direction --------------------------------------------------
    prev = state.get(task.name)
    if force_sync:
        # Ignore saved state — always sync (pick whichever side is newer)
        direction = determine_direction(
            win_side=win_status,
            linux_side=linux_status,
            strategy=strategy,
            last_sync_ts=None,
            last_win_mtime=None,
            last_linux_mtime=None,
        )
    else:
        direction = determine_direction(
            win_side=win_status,
            linux_side=linux_status,
            strategy=strategy,
            last_sync_ts=prev.last_sync_ts if prev else None,
            last_win_mtime=prev.last_win_mtime if prev else None,
            last_linux_mtime=prev.last_linux_mtime if prev else None,
        )

    # -- act on direction -----------------------------------------------------
    if direction == Direction.IN_SYNC:
        return f"[OK] {task.name} — already in sync"

    if direction == Direction.SPLIT_BRAIN:
        _backup_target(linux_dir if is_linux else win_dir, task)
        return (
            f"[CONFLICT] {task.name} — split-brain detected, "
            "target backed up to .bak, aborting"
        )

    if direction == Direction.UNKNOWN:
        return f"[SKIP] {task.name} — neither side has data"

    # Map direction to concrete source / target
    if direction == Direction.WIN_TO_LINUX:
        source_dir, target_dir = win_dir, linux_dir
        source_status, target_status = win_status, linux_status
        to_linux = True
    else:  # LINUX_TO_WIN
        source_dir, target_dir = linux_dir, win_dir
        source_status, target_status = linux_status, win_status
        to_linux = False

    if not source_status.exists:
        return f"[SKIP] {task.name} — source {source_dir} does not exist"

    # -- safety: writable check -----------------------------------------------
    target_parent = target_dir.parent
    if target_parent.exists() and not os.access(target_parent, os.W_OK):
        return (
            f"[ERROR] {task.name} — "
            f"target parent {target_parent} is read-only, aborting"
        )

    if dry_run:
        return (
            f"[DRY-RUN] {task.name} — "
            f"would sync {source_dir} → {target_dir} ({direction.name})"
        )

    # -- atomic copy ----------------------------------------------------------
    tmp_dir = target_dir.parent / TMP_DIR_NAME
    _atomic_copy(
        source_dir=source_dir,
        target_dir=target_dir,
        tmp_dir=tmp_dir,
        task=task,
        mappings=mappings,
        to_linux=to_linux,
    )

    # -- re-scan target after copy, then update state -------------------------
    target_status_after = scan_side(target_dir, task.target_extensions)
    if to_linux:
        state.update(
            task_name=task.name,
            win_mtime=source_status.max_mtime,
            linux_mtime=target_status_after.max_mtime,
        )
    else:
        state.update(
            task_name=task.name,
            win_mtime=target_status_after.max_mtime,
            linux_mtime=source_status.max_mtime,
        )

    return f"[SYNCED] {task.name} — {source_dir} → {target_dir}"


# ======================================================================
#  Helpers
# ======================================================================


def _init_state(config: AppConfig) -> StateManager:
    state_path = Path(config.settings.state_file_path).expanduser().resolve()
    sm = StateManager(state_path)
    sm.load()
    return sm


def _filter_tasks(config: AppConfig, task_filter: str | None) -> list[TaskConfig]:
    if task_filter is None:
        return list(config.tasks)
    return [t for t in config.tasks if t.name == task_filter]


def _backup_target(target_dir: Path, task: TaskConfig) -> None:
    """Rename *target_dir* to ``<name>.bak`` to preserve conflicting data."""
    if not target_dir.exists():
        return
    bak = target_dir.with_name(target_dir.name + ".bak")
    if bak.exists():
        shutil.rmtree(bak)
    os.rename(target_dir, bak)
    logger.warning("Split-brain backup: %s → %s", target_dir, bak)


def _atomic_copy(
    source_dir: Path,
    target_dir: Path,
    tmp_dir: Path,
    task: TaskConfig,
    mappings: list[BaseMapping],
    to_linux: bool,
) -> None:
    """Copy *source_dir* → *tmp_dir*, replace paths, then ``os.replace``."""
    # Clean up any stale temp dir
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)

    # Copy everything
    shutil.copytree(source_dir, tmp_dir)

    # Post-process text files
    _replace_in_tree(tmp_dir, task.target_extensions, mappings, to_linux)

    # Atomic swap
    if target_dir.exists():
        shutil.rmtree(target_dir)
    os.replace(tmp_dir, target_dir)


def _replace_in_tree(
    root: Path,
    extensions: list[str],
    mappings: list[BaseMapping],
    to_linux: bool,
) -> None:
    """Walk *root* and apply content replacement to matching files."""
    ext_set = {e.lower() for e in extensions}

    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if not any(fn.lower().endswith(ext) for ext in ext_set):
                continue
            fp = Path(dirpath) / fn
            raw = fp.read_text(encoding="utf-8")

            if fn.endswith(".jsonl"):
                processed = replace_in_jsonl_text(
                    raw, mappings, to_linux=to_linux
                )
            else:  # .json
                processed = replace_in_json_text(
                    raw, mappings, to_linux=to_linux
                )

            fp.write_text(processed, encoding="utf-8")
