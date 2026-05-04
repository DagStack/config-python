"""Env interpolation for YAML values.

Per spec ADR-0001 §2:
    ${ENV_VAR}            → value of ENV_VAR, error if not set
    ${ENV_VAR:-default}   → value of ENV_VAR, or literal "default" if not set or empty

Semantics:
- The interpolated value is always a string. Type coercion happens
  later, in getString/getInt.
- Escape a literal `$`: `$$` → `$`.
- An unresolved `${VAR}` without a default → `ConfigError(reason=ENV_UNRESOLVED)`.
- The default value is a **literal string**; nested `${...}` in defaults
  is NOT interpolated (spec §2).
  `${FOO:-${BAR}}` → default=`"${BAR"` (up to the first `}`), with the
  trailing `}` remaining as a literal.

The parser is a state machine (a regex-based approach breaks on escape
edge cases like `$${A}`, where `$$` escapes the dollar but `{A}` does
not become an interpolation — Compose-style semantics).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from dagstack.config.errors import ConfigError, ConfigErrorReason

if TYPE_CHECKING:
    from collections.abc import Mapping

# Public regex for has_interpolation() — an approximate detector, not a full parser.
_DETECT_RE = re.compile(r"\$\{[^}]*\}")


def interpolate(
    text: str,
    env: Mapping[str, str],
    *,
    path: str = "",
    source_id: str | None = None,
) -> str:
    """Substitute `${VAR}` / `${VAR:-default}` in text using values from env.

    Args:
        text: Raw string (usually the contents of a YAML/JSON file).
        env: Mapping of env vars (usually `os.environ`, but can be a mock).
        path: Optional dot-notation path for error reporting.
        source_id: Optional source id for error reporting.

    Returns:
        A string with placeholders substituted.

    Raises:
        ConfigError: when `${VAR}` without a default is encountered and
            VAR is missing from env.
    """
    result: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "$" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "$":
                # Escape: `$$` → literal `$`. Consume both.
                result.append("$")
                i += 2
                continue
            if nxt == "{":
                close = text.find("}", i + 2)
                if close == -1:
                    # No closing brace — treat `${` as literal. Soft-fail
                    # on malformed input (e.g., a non-standard use of `${`
                    # in the text).
                    result.append(ch)
                    i += 1
                    continue
                expr = text[i + 2 : close]
                result.append(_resolve_expr(expr, env, path=path, source_id=source_id))
                i = close + 1
                continue
        # Literal character.
        result.append(ch)
        i += 1
    return "".join(result)


def _resolve_expr(
    expr: str,
    env: Mapping[str, str],
    *,
    path: str,
    source_id: str | None,
) -> str:
    """Resolve a `${...}` body: `VAR` or `VAR:-default`.

    `${secret:<scheme>:<path>...}` tokens are RESERVED for the Phase 2
    SecretSource path (ADR-0002 §1) — they are emitted verbatim by this
    function so the post-YAML tree walker can convert them to
    `SecretRef` placeholders. The Phase 1 env interpolator deliberately
    does not interpret them; that is the SecretSource's job.
    """
    if expr.startswith("secret:"):
        # Verbatim — re-emit the original token shell so the tree
        # walker (`_secret_grammar.walk_secret_refs`) can pick it up
        # after YAML parsing.
        return "${" + expr + "}"
    if ":-" in expr:
        var_name, default = expr.split(":-", 1)
        var_name = var_name.strip()
        value = env.get(var_name)
        # An empty string triggers the default (per spec §2: "not set or empty").
        return value if value else default
    var_name = expr.strip()
    value = env.get(var_name)
    if value is None:
        raise ConfigError(
            path=path,
            reason=ConfigErrorReason.ENV_UNRESOLVED,
            details=f"env variable {var_name!r} is not set and no default provided",
            source_id=source_id,
        )
    return value


def has_interpolation(text: str) -> bool:
    """Quick check: does the string contain `${...}` placeholders.

    Does not validate syntax — just matches the pattern. Used as an
    optimization (skip the parser pass if there are no placeholders).
    """
    return _DETECT_RE.search(text) is not None
