"""Canonical JSON serializer (RFC 8785 subset per spec §9.1.1).

Per spec ADR-0001 §9.1.1 (v2.3):
- Sorted object keys (lexicographic UTF-16 code-unit order per RFC 8785
  §3.2.3). Python's native ``sorted()`` orders by UTF-32 code-point — for
  BMP keys the two coincide, but they diverge on supplementary-plane code
  points (anything ≥ U+10000). Concretely, U+E000 (BMP private-use area)
  must sort *after* U+20000 (CJK Ext B) because U+20000 is represented as
  the high-surrogate D840 in UTF-16, and 0xD840 < 0xE000; Python's UTF-32
  sort would put U+E000 first. The cross-binding fixture
  ``conformance/canonical_json/key_order_drift_witness.json`` pins this.
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
    # ``sort_keys=False`` is intentional: ``_normalize`` already inserts
    # dict entries in UTF-16 code-unit sorted order. Letting json.dumps
    # re-sort with its built-in ``sort_keys=True`` would fall back to
    # Python's UTF-32 code-point comparison, breaking parity on
    # supplementary-plane keys.
    # ``ensure_ascii=False`` is required to keep non-ASCII keys as raw
    # UTF-8 bytes; the alternative ``\uXXXX`` escape would change the
    # byte sequence and break byte-equality with the other bindings.
    return json.dumps(
        normalized,
        sort_keys=False,
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


def _utf16_sort_key(s: str) -> bytes:
    """RFC 8785 §3.2.3 sort key: encode the string as UTF-16-BE bytes.

    Bytewise tuple comparison on UTF-16-BE bytes is equivalent to
    lexicographic comparison of the underlying UTF-16 code-unit sequence,
    which matches both the RFC and the native JavaScript / Go (utf16)
    behaviour. The big-endian variant is chosen so that the leading byte
    of each code unit is the high half, preserving the ordering of single
    code units across the byte sequence.
    """
    return s.encode("utf-16-be")


def _normalize(obj: Any) -> Any:
    """Recursively normalize edge cases before json.dumps.

    - Dict keys are inserted in UTF-16 code-unit sorted order (per
      :func:`_utf16_sort_key`), so the downstream ``json.dumps`` with
      ``sort_keys=False`` preserves the canonical sort.
    - ``-0.0`` → ``0`` integer form (RFC 8785 §3.2.2.3 + v2.1 whole-number
      float clarification).
    - Validation: dict keys must be ``str`` (JSON spec).
    - NaN / Infinity detection → ValueError up-front.

    Recursively walks dict/list; other types are returned as-is (json.dumps
    handles them further or raises TypeError).
    """
    if isinstance(obj, dict):
        items: list[tuple[str, Any]] = []
        for key, value in obj.items():
            if not isinstance(key, str):
                raise ValueError(f"non-string dict key not allowed in canonical JSON: {key!r}")
            items.append((key, value))
        items.sort(key=lambda kv: _utf16_sort_key(kv[0]))
        result: dict[str, Any] = {}
        for key, value in items:
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
