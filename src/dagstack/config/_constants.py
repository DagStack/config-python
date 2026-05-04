"""Internal constants, shared between modules.

A separate module avoids import cycles between `canonical_json.py`,
`sources.py`, and `config.py`, and prevents duplicating values. Not part
of the public API; names use the `_` prefix convention.
"""

from __future__ import annotations

# i-JSON safe integer range per RFC 7493 §2.2 / spec v2.1 §4.3 +
# `_meta/coercion.yaml`.
#
# A whole-number float in this range is representable as int without loss
# of precision and is used in three places:
#   - `get_int` accept branch for whole-number floats
#   - canonical JSON emission: whole-number float → integer form
#   - `_normalize_numbers`: YAML/JSON load-time normalization of
#     whole-number float → int.
IJSON_SAFE_MAX = 2**53 - 1  # 9_007_199_254_740_991
