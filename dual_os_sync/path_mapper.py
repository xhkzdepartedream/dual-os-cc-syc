"""Path mapping — converts Windows ↔ Linux paths using base mapping rules."""

from __future__ import annotations

from .config import BaseMapping


def win_to_linux(win_path: str, mapping: BaseMapping) -> str:
    """Convert a Windows absolute path to its Linux equivalent.

    Example::

        >>> m = BaseMapping(id="x", win="E:\\\\DL\\\\", linux="/home/u/.data2/DL/")
        >>> win_to_linux("E:\\\\DL\\\\vggt\\\\docs\\\\readme.md", m)
        '/home/u/.data2/DL/vggt/docs/readme.md'
    """
    win_base = mapping.win   # E:\DL\
    linux_base = mapping.linux

    # Case-insensitive prefix match
    if not _starts_with_ignore_case(win_path, win_base):
        raise ValueError(
            f"Path {win_path!r} does not start with base {win_base!r}"
        )

    rest = win_path[len(win_base):]
    return linux_base + rest.replace("\\", "/")


def linux_to_win(linux_path: str, mapping: BaseMapping) -> str:
    """Convert a Linux absolute path to its Windows equivalent.

    Example::

        >>> m = BaseMapping(id="x", win="E:\\\\DL\\\\", linux="/home/u/.data2/DL/")
        >>> linux_to_win("/home/u/.data2/DL/vggt/docs/readme.md", m)
        'E:\\\\DL\\\\vggt\\\\docs\\\\readme.md'
    """
    linux_base = mapping.linux
    win_base = mapping.win

    if not linux_path.startswith(linux_base):
        raise ValueError(
            f"Path {linux_path!r} does not start with base {linux_base!r}"
        )

    rest = linux_path[len(linux_base):]
    return win_base + rest.replace("/", "\\")


# ---------------------------------------------------------------------------
#  Internal helpers
# ---------------------------------------------------------------------------

def _starts_with_ignore_case(text: str, prefix: str) -> bool:
    """Return True if *text* starts with *prefix*, ignoring case."""
    return len(text) >= len(prefix) and text[:len(prefix)].lower() == prefix.lower()
