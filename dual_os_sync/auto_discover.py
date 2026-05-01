"""Auto-discover sync tasks by scanning both sides' .claude/projects/ directories.

Replaces the need to manually list ``tasks`` in ``config.yaml``.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

from .config import AppConfig, BaseMapping, TaskConfig

logger = logging.getLogger(__name__)

# Relative paths inside each user home
WIN_CLAUDE_PROJECTS = "C:\\Users\\30413\\.claude\\projects"
LINUX_CLAUDE_PROJECTS = "~/.claude/projects"


# ======================================================================
#  Reading cwd from project files
# ======================================================================


def read_cwd(project_dir: Path) -> str | None:
    """Read the workspace *cwd* from any ``.jsonl`` file in the project.

    Scans all lines of each file and returns the first ``cwd`` field found.
    Returns ``None`` if the project directory is empty or unreadable.
    """
    if not project_dir.is_dir():
        return None
    for f in sorted(project_dir.iterdir()):
        if f.suffix == ".jsonl" and f.is_file():
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    cwd = obj.get("cwd")
                    if cwd:
                        return cwd
            except Exception:
                continue
    return None


# ======================================================================
#  Resolving Windows cwd ↔ Linux path
# ======================================================================


def resolve_linux_path(win_cwd: str, config: AppConfig) -> str | None:
    """Resolve a Windows absolute path to a Linux path.

    Tries, in order:
    1. Existing ``base_mappings`` (most precise)
    2. ``access`` mount-point config (drive letter lookup)
    """
    # Try base mappings first
    for m in config.base_mappings:
        win_prefix = m.win.rstrip("\\").lower()
        if win_cwd.lower().startswith(win_prefix.lower()):
            rel = win_cwd[len(win_prefix):].lstrip("\\")
            return (m.linux + rel.replace("\\", "/")).rstrip("/")

    # Fall back to access mount points
    drive = win_cwd[0].lower()
    rest = win_cwd[2:].replace("\\", "/").lstrip("/")

    mount_map = config.access._mount_map()
    mount = mount_map.get(drive)
    if mount:
        return (str(Path(mount) / rest)).rstrip("/")

    logger.warning("No mount point configured for drive %s:", drive.upper())
    return None


def linux_cwd_to_win(linux_cwd: str, config: AppConfig) -> str | None:
    """Resolve a Linux absolute path back to a Windows path."""
    for m in config.base_mappings:
        if linux_cwd.startswith(m.linux.rstrip("/")):
            rel = linux_cwd[len(m.linux.rstrip("/")):]
            return (m.win.rstrip("\\") + rel.replace("/", "\\")).rstrip("\\")

    # Try reverse-mapping drive mounts
    for drive, mount in config.access._mount_map().items():
        if mount and linux_cwd.startswith(mount):
            rel = linux_cwd[len(mount):]
            return f"{drive.upper()}:{rel.replace('/', '\\')}"

    return None


# ======================================================================
#  Pairing projects across OSes
# ======================================================================


def scan_projects(root: Path) -> dict[str, Path]:
    """Scan a ``.claude/projects/`` directory.

    Returns ``{dir_name: project_dir_path}``.
    Skips directories ending with ``.bak`` to avoid picking up
    split-brain backups as valid projects.
    """
    if not root.is_dir():
        return {}
    return {
        d.name: d for d in root.iterdir()
        if d.is_dir() and not d.name.endswith(".bak")
    }


def find_linux_project_by_cwd(
    linux_cwds: dict[str, str],
    target_linux_path: str,
) -> str | None:
    """Find a Linux project directory whose *cwd* matches *target_linux_path*."""
    for name, cwd in linux_cwds.items():
        if cwd and cwd.rstrip("/") == target_linux_path.rstrip("/"):
            return name
    return None


# ======================================================================
#  Inferring base mappings from matched pairs
# ======================================================================


def infer_base_mapping_from_pair(win_cwd: str, linux_cwd: str) -> Optional[BaseMapping]:
    """Infer a :class:`BaseMapping` from a matched pair of *cwd* values.

    Finds the longest matching tail (by ASCII path components) and strips it
    to derive the base prefix.

    Example::

        win_cwd   = E:\\notes\\计算机图形学-ComputerGraphics
        linux_cwd = /home/xhkzdepartedream/data2/notes/计算机图形学-ComputerGraphics
        → mapping: E:\\notes\\ ↔ /home/xhkzdepartedream/data2/notes/
    """
    win_cwd = win_cwd.rstrip("\\").rstrip("/")
    linux_cwd = linux_cwd.rstrip("/")

    win_parts = win_cwd.replace("/", "\\").split("\\")
    linux_parts = linux_cwd.split("/")

    for i in range(1, min(len(win_parts), len(linux_parts)) + 1):
        win_tail = win_parts[-i:]
        linux_tail = linux_parts[-i:]

        if win_tail != linux_tail:
            continue

        win_prefix = "\\".join(win_parts[:-i]) + "\\"
        linux_prefix = "/".join(linux_parts[:-i]) + "/"

        if len(win_prefix) < 4:
            continue

        try:
            return BaseMapping(
                id=f"auto_{win_prefix[0].lower()}_{len(win_parts)-i}",
                win=win_prefix,
                linux=linux_prefix,
            )
        except Exception:
            continue

    return None


# ======================================================================
#  Linux project directory name encoding (best-effort)
# ======================================================================


def encode_linux_project_dir_name(linux_cwd: str) -> str:
    """Encode a Linux workspace path to a project directory name.

    This is a **best-effort** reconstruction of Claude Code's internal naming.
    It works reliably for ASCII-only paths; non-ASCII characters will cause
    the encoded name to differ from what Claude Code would generate.
    """
    stripped = linux_cwd.lstrip("/")
    encoded = re.sub(r"[^a-zA-Z0-9-]", "-", stripped)
    return "-" + encoded


# ======================================================================
#  Windows path formatting
# ======================================================================


def _format_win_path(project_dir_name: str) -> str:
    """Format a Windows ``.claude/projects/<dir>`` path in Windows style.

    ``sync_engine`` expects ``task.win_path`` to be a native Windows path
    (e.g. ``C:\\Users\\30413\\.claude\\projects\\e--DL-vggt``) so that
    :meth:`AccessConfig.resolve_win_path_on_linux` can translate it.
    """
    return f"C:\\Users\\30413\\.claude\\projects\\{project_dir_name}"


# ======================================================================
#  Main entry point
# ======================================================================


def auto_discover(config: AppConfig) -> tuple[list[TaskConfig], list[BaseMapping]]:
    """Discover sync tasks by scanning project directories on both OSes.

    Returns ``(tasks, inferred_mappings)``.

    1. Resolve the ``.claude/projects/`` paths for both OSes
    2. Scan each to find all project directories
    3. Read ``cwd`` from each project's ``.jsonl`` files
    4. Match Windows projects to Linux projects by resolving through
       ``base_mappings`` or ``access`` mount points
    5. Auto-infer new ``base_mappings`` from matched pairs
    6. Generate :class:`TaskConfig` for each discovered project
    """
    is_linux = sys.platform == "linux"

    # ---- resolve project root directories -----------------------------------
    if is_linux:
        win_root = config.access.resolve_win_path_on_linux(WIN_CLAUDE_PROJECTS)
        linux_root = Path(LINUX_CLAUDE_PROJECTS).expanduser().resolve()
    else:
        win_root = Path(WIN_CLAUDE_PROJECTS).expanduser().resolve()
        linux_raw = config.access.resolve_linux_path_on_windows(LINUX_CLAUDE_PROJECTS)
        if linux_raw is None:
            logger.error("win_mount_linux_root not configured — cannot scan Linux side")
            return [], []
        linux_root = linux_raw.resolve()

    # ---- scan both sides ----------------------------------------------------
    win_projects = scan_projects(win_root)
    linux_projects = scan_projects(linux_root)

    logger.info("Discovered %d Windows project(s), %d Linux project(s)",
                len(win_projects), len(linux_projects))

    # ---- read cwds ----------------------------------------------------------
    win_cwds: dict[str, str] = {}
    for name, path in win_projects.items():
        cwd = read_cwd(path)
        if cwd:
            win_cwds[name] = cwd
        else:
            logger.debug("No cwd found for Windows project %s (empty project)", name)

    linux_cwds: dict[str, str] = {}
    for name, path in linux_projects.items():
        cwd = read_cwd(path)
        if cwd:
            linux_cwds[name] = cwd
        else:
            logger.debug("No cwd found for Linux project %s (empty project)", name)

    # ---- known + inferred mappings ------------------------------------------
    known_mappings: dict[str, BaseMapping] = {}
    for m in config.base_mappings:
        known_mappings[m.id] = m
    inferred_mappings: list[BaseMapping] = []

    # ---- match and build tasks ----------------------------------------------
    tasks: list[TaskConfig] = []

    for win_name, win_cwd in win_cwds.items():
        linux_path = resolve_linux_path(win_cwd, config)
        if linux_path is None:
            logger.warning("Cannot resolve %s to a Linux path — skipping", win_cwd)
            continue

        existing_linux_name = find_linux_project_by_cwd(linux_cwds, linux_path)

        if existing_linux_name:
            logger.info("Matched: %s (%s) ↔ %s (%s)",
                        win_name, win_cwd, existing_linux_name, linux_path)
        else:
            logger.info("New project: %s (%s) → Linux at %s",
                        win_name, win_cwd, linux_path)

        depends_on = None
        for m in known_mappings.values():
            if win_cwd.lower().startswith(m.win.lower()):
                depends_on = m.id
                break

        if depends_on is None and existing_linux_name:
            inferred = infer_base_mapping_from_pair(win_cwd, linux_path)
            if inferred:
                known_mappings[inferred.id] = inferred
                inferred_mappings.append(inferred)
                depends_on = inferred.id
                logger.info("Inferred base mapping: %s ↔ %s", inferred.win, inferred.linux)

        win_dir = _format_win_path(win_name)
        linux_dir_name = existing_linux_name or encode_linux_project_dir_name(linux_path)
        linux_dir = str(Path(LINUX_CLAUDE_PROJECTS).expanduser() / linux_dir_name)

        tasks.append(TaskConfig(
            name=f"auto: {win_cwd}",
            depends_on=depends_on,
            win_path=win_dir,
            linux_path=linux_dir,
            target_extensions=[".jsonl", ".json"],
        ))

    tasks.sort(key=lambda t: 0 if t.depends_on else 1)

    return tasks, inferred_mappings
