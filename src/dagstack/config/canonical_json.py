"""Canonical JSON serializer (RFC 8785 subset per spec §9.1.1).

Per spec ADR-0001 §9.1.1 (v2.1):
- Sorted object keys (lexicographic UTF-8 code-point order).
- No whitespace except inside strings.
- Integers: no decimal point (`1`, not `1.0`).
- **Whole-number floats are emitted in integer form** (`100.0` → `100`,
  `-0.0` → `0`) per v2.1 clarification + `_meta/canonical_json.yaml`.
  Parity with Go `strconv.FormatFloat('g')`.
- Fractional floats: shortest round-trip (Python `float.__repr__`).
- NaN / Infinity / -Infinity are forbidden.
- UTF-8 encoding (enforced for bytes output).
- No trailing newline.

Used for:
- `conformance/expected/*.json` golden fixtures.
- Hash-based dedup (body_hash in logger-spec).
- Diff-based comparison across bindings (bit-identical output).

Whole-number-float → int normalization is applied to values in the
i-JSON safe range (`±(2^53-1)`). Outside this range the float is kept
as-is to avoid losing precision on the round-trip back to float.
"""

from __future__ import annotations

import json
import math
from typing import Any

from dagstack.config._constants import IJSON_SAFE_MAX


def canonical_json_dumps(obj: Any) -> str:
    """Serialize obj to a canonical JSON string.

    Args:
        obj: JSON-serializable value (dict/list/str/int/float/bool/None).

    Returns:
        Canonical JSON Unicode string. For wire formats (file / hash /
        network), additionally encode to UTF-8 via `canonical_json_dumpb`.

    Raises:
        ValueError: NaN / ±Infinity in floats, non-string keys in a dict,
            or a non-JSON-serializable type.
    """
    normalized = _normalize(obj)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=False,
    )


def canonical_json_dumpb(obj: Any) -> bytes:
    """Serialize obj to canonical JSON UTF-8 bytes.

    Shortcut for `canonical_json_dumps(obj).encode("utf-8")` — the typical
    wire form.
    """
    return canonical_json_dumps(obj).encode("utf-8")


def _normalize(obj: Any) -> Any:
    """Recursively normalize edge cases before json.dumps.

    - `-0.0` → `0.0` (RFC 8785 §3.2.2.3).
    - Validation: dict keys must be str (JSON spec).
    - NaN / Infinity detection → ValueError up-front, with the exact path
      in the error.

    Recursively walks dict/list; other types are returned as-is (json.dumps
    handles them further or raises TypeError).
    """
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                raise ValueError(f"non-string dict key not allowed in canonical JSON: {key!r}")
            result[key] = _normalize(value)
        return result
    if isinstance(obj, list):
        return [_normalize(item) for item in obj]
    if isinstance(obj, bool):
        # bool must be checked before int (bool is a subclass of int in Python).
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise ValueError(f"NaN / Infinity not allowed in canonical JSON: {obj!r}")
        # Whole-number float in the i-JSON safe range → integer form
        # (`100.0` → `100`). Special case for `-0.0`: `(-0.0).is_integer()`
        # is True, `abs(-0.0) == 0 <= SAFE_MAX`, `int(-0.0) == 0` →
        # we emit `"0"`. This is a normalize step per §9.1.1 /
        # canonical_json.yaml, not a passthrough from `FormatFloat('g')`
        # (Go would yield `-0`); the spec explicitly requires `0` for
        # negative zero. Fractional and out-of-range floats are kept as-is.
        if obj.is_integer() and abs(obj) <= IJSON_SAFE_MAX:
            return int(obj)
        return obj
    return obj
