"""Secret field masking per ADR-0001 v2.2 §6 / `_meta/secret_patterns.yaml`.

Defines which field names are considered sensitive and replaced with
`[MASKED]` in diagnostic output. The patterns' source of truth is the
spec submodule `spec/_meta/secret_patterns.yaml`; here they are
hard-copied constants (to be emitter-generated in the future).
"""

from __future__ import annotations

from typing import Any

MASKED_PLACEHOLDER = "[MASKED]"

# Mirror of spec/_meta/secret_patterns.yaml (v2.2).
# When the spec updates, bump these in sync.
_SECRET_SUFFIXES: frozenset[str] = frozenset(
    (
        "_key",
        "_secret",
        "_token",
        "_password",
        "_passphrase",
        "_credentials",
        "_credential",
        "_auth",
        "_api_key",
        "_access_key",
        "_private_key",
    )
)

_SECRET_PREFIXES: frozenset[str] = frozenset(
    (
        "api_key",
        "api_token",
        "secret",
        "password",
        "private_key",
        "access_token",
        "bearer",
    )
)

_SECRET_EXACT: frozenset[str] = frozenset(
    (
        "api_key",
        "apikey",
        "password",
        "passwd",
        "pw",
        "token",
        "secret",
        "credentials",
    )
)


def is_secret_field(name: str) -> bool:
    """True if the field name matches the secret patterns.

    Check order (OR semantics): suffix → prefix → exact.
    Case-insensitive.
    """
    lowered = name.lower()
    if lowered in _SECRET_EXACT:
        return True
    if any(lowered.endswith(suffix) for suffix in _SECRET_SUFFIXES):
        return True
    return any(lowered.startswith(prefix) for prefix in _SECRET_PREFIXES)


def mask_value(name: str, value: Any) -> Any:
    """Replace value with MASKED_PLACEHOLDER if name is a secret and value is non-empty.

    Empty / null values are not masked — there is nothing to hide;
    returning "[MASKED]" instead of None would be misleading.
    """
    if not is_secret_field(name):
        return value
    if value is None or value == "":
        return value
    return MASKED_PLACEHOLDER
