"""Sync-direction detection — compares mtimes across two directory trees."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path


# Tolerance for cross-filesystem mtime comparisons.
#
# NTFS (via ntfs3 kernel driver or FUSE) may round timestamps during
# FILETIME <-> timespec64 conversion, introducing sub-second drift of up
# to ~100 ns on ntfs3 or ~1 s on older FUSE mounts.  We apply a small
# epsilon so these tiny artifacts don't trigger spurious re-syncs.
_MTIME_EPSILON: float = 1.0


class Direction(Enum):
    """Possible sync outcomes."""

    WIN_TO_LINUX = auto()   # Windows side is newer → sync to Linux
    LINUX_TO_WIN = auto()   # Linux side is newer → sync to Windows
    IN_SYNC = auto()        # Both sides match the last sync state
    SPLIT_BRAIN = auto()    # Both sides changed since last sync
    UNKNOWN = auto()        # No prior state — first sync


@dataclass
class SideStatus:
    """Mtime snapshot for one side of a sync task."""

    path: Path
    exists: bool
    max_mtime: float = 0.0       # newest mtime among target-extension files
    file_count: int = 0


def scan_side(root: Path, extensions: list[str]) -> SideStatus:
    """Recursively scan *root* for files matching *extensions*.

    Returns a ``SideStatus`` with the newest ``mtime`` found.
    """
    if not root.exists():
        return SideStatus(path=root, exists=False)

    max_mtime = 0.0
    count = 0
    ext_set = {e.lower() for e in extensions}

    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if not any(fn.lower().endswith(ext) for ext in ext_set):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                mtime = os.path.getmtime(fp)
            except OSError:
                continue
            if mtime > max_mtime:
                max_mtime = mtime
            count += 1

    return SideStatus(path=root, exists=True, max_mtime=max_mtime, file_count=count)


def determine_direction(
    win_side: SideStatus,
    linux_side: SideStatus,
    strategy: str,
    last_sync_ts: float | None,
    last_win_mtime: float | None,
    last_linux_mtime: float | None,
) -> Direction:
    """Decide which direction to sync.

    Args:
        win_side: Status of the Windows-side directory.
        linux_side: Status of the Linux-side directory.
        strategy: One of ``"auto_newest"``, ``"force_win_to_linux"``,
                  ``"force_linux_to_win"``.
        last_sync_ts: Epoch timestamp of the last successful sync.
        last_win_mtime: Windows max-mtime recorded at last sync.
        last_linux_mtime: Linux max-mtime recorded at last sync.

    Returns:
        The recommended sync direction.
    """
    # -- forced strategies ---------------------------------------------------
    if strategy == "force_win_to_linux":
        if not win_side.exists:
            raise FileNotFoundError(f"Windows source not found: {win_side.path}")
        return Direction.WIN_TO_LINUX

    if strategy == "force_linux_to_win":
        if not linux_side.exists:
            raise FileNotFoundError(f"Linux source not found: {linux_side.path}")
        return Direction.LINUX_TO_WIN

    # -- auto_newest ---------------------------------------------------------
    # First sync ever — pick the side that exists and has files.
    if last_sync_ts is None:
        if win_side.exists and linux_side.exists:
            if win_side.max_mtime >= linux_side.max_mtime:
                return Direction.WIN_TO_LINUX
            return Direction.LINUX_TO_WIN
        if win_side.exists:
            return Direction.WIN_TO_LINUX
        if linux_side.exists:
            return Direction.LINUX_TO_WIN
        return Direction.UNKNOWN

    # Detect changes since last sync.
    win_changed = (
        win_side.exists
        and last_win_mtime is not None
        and win_side.max_mtime > last_win_mtime + _MTIME_EPSILON
    )
    linux_changed = (
        linux_side.exists
        and last_linux_mtime is not None
        and linux_side.max_mtime > last_linux_mtime + _MTIME_EPSILON
    )

    if win_changed and linux_changed:
        return Direction.SPLIT_BRAIN

    if win_changed:
        return Direction.WIN_TO_LINUX

    if linux_changed:
        return Direction.LINUX_TO_WIN

    return Direction.IN_SYNC
