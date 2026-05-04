"""Deep merge for ConfigTree.

Per spec ADR-0001 §3: maps are merged deeply; sequences (lists) are
**replaced atomically**, not concatenated. To change an array, the
override file must contain the entire array.

Resolution order = list order in `loadFrom([sources])`: lowest priority
first.

All functions return **new** structures — inputs are not mutated, and
nested containers are not shared with the inputs (safe for further
modification by the caller).
"""

from __future__ import annotations

from typing import Any

# ConfigTree — a nested map / sequence / scalar. Type alias for docs; at
# runtime this is `dict[str, Any]` / `list[Any]` / `str|int|float|bool|None`.
ConfigTree = dict[str, Any]


def deep_merge(base: ConfigTree, override: ConfigTree) -> ConfigTree:
    """Recursively merge `override` into `base`, returning a new dict.

    Rules:
    - Keys from override that are absent from base — added.
    - Keys from base that are absent from override — preserved.
    - Keys present in both: if both values are dicts, recurse; otherwise
      override wins.
    - Lists are **replaced atomically** (not concatenated, not merged
      element-wise).

    All nested containers are deep-copied; references are not shared with
    the arguments.
    """
    result: ConfigTree = {}
    for key, value in base.items():
        result[key] = _deep_copy(value)
    for key, override_value in override.items():
        base_value = result.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            result[key] = deep_merge(base_value, override_value)
        else:
            result[key] = _deep_copy(override_value)
    return result


def deep_merge_all(trees: list[ConfigTree]) -> ConfigTree:
    """Merge multiple trees in priority order (lowest first).

    Returns an empty dict for an empty list. For a singleton list returns
    a deep copy.
    """
    if not trees:
        return {}
    result = deep_merge({}, trees[0])
    for next_tree in trees[1:]:
        result = deep_merge(result, next_tree)
    return result


def _deep_copy(value: Any) -> Any:
    """Recursively copy nested containers (dict/list). Scalars are returned as-is."""
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    return value
