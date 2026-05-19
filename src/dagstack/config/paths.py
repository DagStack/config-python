r"""Path navigation for the config tree.

Per spec ADR-0001 §4.2: dot-notation paths, array indexing `[N]`.
Examples: `database.host`, `cache.region.host`, `dagstack.plugin_dirs[0]`.

Phase 1 scope:
- Dot-separated keys: `a.b.c`.
- Zero-based array indexing: `a.b[0]`, `a[0][1]`.
- Combinations: `a.b[0].c`.

**Not covered** (open question, Phase 2+):
- Escaped dots in keys (``labels.kubernetes\.io/zone``) — spec §4.2
  envisions a backslash escape, but a per-language binding may defer
  until the first reporter requests it.
"""

from __future__ import annotations

import re
from typing import Any

from dagstack.config.errors import ConfigError, ConfigErrorReason

# Matches a path segment: name, name[N], or [N] (a bare index after name[N]).
_SEGMENT_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def parse_path(path: str) -> list[str | int]:
    """Parse a dot-path into a list of segments.

    Examples:
        >>> parse_path("a.b.c")
        ['a', 'b', 'c']
        >>> parse_path("cache.region.host")
        ['cache', 'region', 'host']
        >>> parse_path("plugins[0].name")
        ['plugins', 0, 'name']
        >>> parse_path("matrix[0][1]")
        ['matrix', 0, 1]

    Returns:
        A list of segments: `str` for map keys, `int` for array indices.

    Raises:
        ValueError: empty path or invalid syntax. Calling code (Config
            methods) typically converts this into a ConfigError.
    """
    if not path:
        raise ValueError("path is empty")

    segments: list[str | int] = []
    cursor = 0
    length = len(path)

    while cursor < length:
        ch = path[cursor]
        # A dot between segments — skip it.
        if ch == ".":
            cursor += 1
            continue
        match = _SEGMENT_RE.match(path, cursor)
        if match is None:
            raise ValueError(f"invalid path syntax at offset {cursor}: {path!r}")
        name, index = match.groups()
        if name is not None:
            segments.append(name)
        else:
            segments.append(int(index))
        cursor = match.end()

    if not segments:
        raise ValueError(f"path produced no segments: {path!r}")
    return segments


def navigate(tree: Any, path: str) -> Any:
    """Walk path through tree and return the value found.

    Args:
        tree: Config tree (nested dict/list/scalar).
        path: Dot-notation path.

    Returns:
        The value at the path.

    Raises:
        ConfigError(MISSING): the path does not lead to an existing
            key/index.
        ConfigError(TYPE_MISMATCH): an intermediate segment does not
            match the expected type (e.g. an array index on a dict or
            vice versa).
    """
    try:
        segments = parse_path(path)
    except ValueError as e:
        raise ConfigError(
            path=path,
            reason=ConfigErrorReason.MISSING,
            details=f"cannot parse path: {e}",
        ) from e

    # ADR-0001 v2.1 §4.5 Path preservation: for `missing` / `type_mismatch`
    # path = the full user-provided path (not the traversed prefix up to
    # the failing point). The `details` field includes the specific
    # failing segment.
    current: Any = tree
    traversed: list[str | int] = []
    for segment in segments:
        traversed.append(segment)
        failing_path = _format_traversed(traversed)
        if isinstance(segment, str):
            if not isinstance(current, dict):
                raise ConfigError(
                    path=path,
                    reason=ConfigErrorReason.TYPE_MISMATCH,
                    details=(
                        f"expected object (map) at {failing_path!r} to index by key, "
                        f"got {type(current).__name__}"
                    ),
                )
            if segment not in current:
                raise ConfigError(
                    path=path,
                    reason=ConfigErrorReason.MISSING,
                    details=(
                        f"key {segment!r} not found at {failing_path!r} (part of requested path)"
                    ),
                )
            current = current[segment]
        else:  # int — array index
            if not isinstance(current, list):
                raise ConfigError(
                    path=path,
                    reason=ConfigErrorReason.TYPE_MISMATCH,
                    details=(
                        f"expected array at {failing_path!r} to index by [{segment}], "
                        f"got {type(current).__name__}"
                    ),
                )
            if segment < 0 or segment >= len(current):
                raise ConfigError(
                    path=path,
                    reason=ConfigErrorReason.MISSING,
                    details=(
                        f"index [{segment}] out of range [0..{len(current)}) at {failing_path!r}"
                    ),
                )
            current = current[segment]
    return current


def _format_traversed(segments: list[str | int]) -> str:
    """Reconstruct a path string from segments for diagnostic messages."""
    parts: list[str] = []
    for seg in segments:
        if isinstance(seg, int):
            parts.append(f"[{seg}]")
        else:
            if parts:
                parts.append(".")
            parts.append(seg)
    return "".join(parts)
