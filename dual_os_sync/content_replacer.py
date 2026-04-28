"""Content replacer — walks parsed JSON and replaces paths in string values."""

from __future__ import annotations

import json
import re
from typing import Any

from .config import BaseMapping
from .path_mapper import linux_to_win, win_to_linux


def replace_in_jsonl_text(
    text: str,
    mappings: list[BaseMapping],
    *,
    to_linux: bool = True,
) -> str:
    """Process multi-line JSONL content, replacing paths line by line.

    Each line is parsed as JSON, walked, and re-serialised.  Lines that
    fail to parse are kept verbatim.

    Args:
        text: Raw JSONL content (one JSON object per line).
        mappings: Base-mapping rules to apply.
        to_linux: ``True`` → Windows paths become Linux paths;
                  ``False`` → the reverse.

    Returns:
        Processed JSONL content.
    """
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append(stripped)
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            out.append(stripped)
            continue
        obj = _walk(obj, mappings, to_linux)
        out.append(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(out)


def replace_in_json_text(
    text: str,
    mappings: list[BaseMapping],
    *,
    to_linux: bool = True,
) -> str:
    """Process a single JSON document, replacing paths throughout.

    Args:
        text: Raw JSON content.
        mappings: Base-mapping rules to apply.
        to_linux: ``True`` → Windows paths become Linux paths;
                  ``False`` → the reverse.

    Returns:
        Processed JSON content.
    """
    obj = json.loads(text)
    obj = _walk(obj, mappings, to_linux)
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ======================================================================
#  Internal walker
# ======================================================================

def _walk(node: Any, mappings: list[BaseMapping], to_linux: bool) -> Any:
    """Recursively walk *node*, replacing path strings in-place."""
    if isinstance(node, dict):
        return {k: _walk(v, mappings, to_linux) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk(item, mappings, to_linux) for item in node]
    if isinstance(node, str):
        return _replace_str(node, mappings, to_linux)
    return node


def _replace_str(value: str, mappings: list[BaseMapping], to_linux: bool) -> str:
    """Attempt to replace *value* if it looks like a known path."""
    if to_linux:
        for m in mappings:
            if _starts_with_ignore_case(value, m.win):
                return win_to_linux(value, m)
    else:
        for m in mappings:
            if value.startswith(m.linux):
                return linux_to_win(value, m)

    # Not a pure path value — find and replace paths embedded within text
    return _replace_embedded_paths(value, mappings, to_linux)


def _replace_embedded_paths(
    value: str, mappings: list[BaseMapping], to_linux: bool
) -> str:
    """Find and replace known paths embedded inside a string value.

    Handles paths in ``<ide_selection>`` / ``<ide_opened_file>`` blocks and user
    messages where the path appears mid-text rather than as a standalone string.
    """
    result = value

    for m in mappings:
        if to_linux:
            win_base = m.win
            linux_base = m.linux
            fwd_base = win_base.replace("\\", "/")

            escaped_bs = re.escape(win_base)
            # Build alternation of backslash and forward-slash variants, case-insensitive
            if fwd_base != win_base:
                escaped_fs = re.escape(fwd_base)
                pattern = f'(?:{escaped_bs}|{escaped_fs})[a-zA-Z0-9_\\-./\\\\]*'
            else:
                pattern = f'{escaped_bs}[a-zA-Z0-9_\\-./\\\\]*'

            def _to_linux(match, wb=win_base, fb=fwd_base, lb=linux_base):
                path = match.group(0)
                if path[:len(wb)].lower() == wb.lower():
                    rest = path[len(wb):]
                elif fb and path[:len(fb)].lower() == fb.lower():
                    rest = path[len(fb):]
                else:
                    return path
                return lb + rest.replace("\\", "/")

            result = re.sub(pattern, _to_linux, result, flags=re.IGNORECASE)
        else:
            linux_base = m.linux
            win_base = m.win

            escaped = re.escape(linux_base)
            pattern = f'{escaped}[a-zA-Z0-9_\\-./\\\\]*'

            def _to_win(match, wb=win_base, lb=linux_base):
                path = match.group(0)
                rest = path[len(lb):]
                return wb + rest.replace("/", "\\")

            result = re.sub(pattern, _to_win, result)

    return result


def _starts_with_ignore_case(text: str, prefix: str) -> bool:
    return len(text) >= len(prefix) and text[: len(prefix)].lower() == prefix.lower()
