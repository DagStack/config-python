"""Internal parser for ``${secret:<scheme>:<path>[?query][#field][:-default]}``.

Implements the grammar from ADR-0002 §1 v1.1 + `_meta/secret_ref_grammar.yaml`.
The single public entry point is `parse_secret_ref` — given the inner
content of one ``${secret:...}`` token (the bytes between ``${secret:``
and ``}``), it returns a `SecretRef` placeholder.

The outer-token regex (`SECRET_REF_OUTER`) is exposed for the YAML
interpolator: a YAML string with multiple references is scanned with
this pattern, and each match's group(1) is fed to `parse_secret_ref`.

Escape rules per ADR-0002 v1.1 §1:
- `##` → literal `#` inside path
- `??` → literal `?` inside path
- `::-` → literal `:-` inside path
- query_value uses RFC 3986 percent-encoding (unquote handles it)

This module is internal — application code uses the resulting
`SecretRef` indirectly through `Config.get_string` etc.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from dagstack.config.errors import ConfigError, ConfigErrorReason
from dagstack.config.secrets import SecretRef

__all__ = ["SECRET_REF_OUTER", "parse_secret_ref", "walk_secret_refs"]


# Outer envelope: matches the WHOLE token shell. group(1) is the inner
# content. Pattern matches `_meta/secret_ref_grammar.yaml` field
# `regex_outer.python` byte-for-byte.
SECRET_REF_OUTER = re.compile(r"\$\{secret:([^}]*)\}")

# Scheme grammar: ADR-0002 §1.
_SCHEME_RE = re.compile(r"[a-z][a-z0-9_]*")


def parse_secret_ref(inner: str, *, origin_source: str = "") -> SecretRef:
    """Parse the inner content of one ``${secret:...}`` token.

    Args:
        inner: The string between ``${secret:`` and ``}`` (no braces).
            Example: ``vault:secret/dagstack/prod/db?version=3#password:-fallback``.
        origin_source: Diagnostic identifier of the source that emitted
            this token (typically a `ConfigSource.id`).

    Returns:
        `SecretRef` placeholder ready for lazy resolution.

    Raises:
        ConfigError(reason=PARSE_ERROR): malformed token (invalid scheme,
            missing scheme separator, unclosed escape, etc.).
    """
    # Step 1 — split off the optional ":-default" tail. Honour the
    # "::-" escape: a literal ":-" inside path is written as "::-" so
    # the parser must not treat that as a default-separator.
    path_with_query_field, default = _split_default(inner)

    # Step 2 — split scheme from the rest. The first ":" terminates
    # scheme; ":" inside path is allowed and escape-free (only "?" and
    # "#" need doubling inside path; ":" stays literal).
    scheme_end = path_with_query_field.find(":")
    if scheme_end < 0:
        raise ConfigError(
            path="",
            reason=ConfigErrorReason.PARSE_ERROR,
            details=(
                f"secret reference missing ':' between scheme and path: '${{secret:{inner}}}'"
            ),
        )
    scheme = path_with_query_field[:scheme_end]
    path_part = path_with_query_field[scheme_end + 1 :]

    # Step 3 — validate scheme grammar.
    if not _SCHEME_RE.fullmatch(scheme):
        raise ConfigError(
            path="",
            reason=ConfigErrorReason.PARSE_ERROR,
            details=(
                f"secret reference scheme '{scheme}' does not match "
                f"[a-z][a-z0-9_]*: '${{secret:{inner}}}'"
            ),
        )

    # Step 4 — split off the optional "#field" projection. Honour "##".
    path_with_query, field_proj = _split_field(path_part)

    # Step 5 — split off the optional "?query".
    path_only, query = _split_query(path_with_query)

    # Step 6 — unescape the path: "??" -> "?", "##" -> "#", "::-" -> ":-".
    path_unescaped = path_only.replace("??", "\x00").replace("##", "\x01").replace("::-", "\x02")
    if "?" in path_unescaped or "#" in path_unescaped:
        # An unescaped "?" or "#" in path is a parse error — they are
        # structural separators and MUST be doubled to appear literal.
        bad = "?" if "?" in path_unescaped else "#"
        raise ConfigError(
            path="",
            reason=ConfigErrorReason.PARSE_ERROR,
            details=(
                f"unescaped '{bad}' in secret reference path "
                f"(use '{bad * 2}' for a literal '{bad}'): "
                f"'${{secret:{inner}}}'"
            ),
        )
    path_unescaped = path_unescaped.replace("\x00", "?").replace("\x01", "#").replace("\x02", ":-")

    # Compose the canonical path: <unescaped-path>[?query][#field].
    # The path stored in SecretRef is the form the loader uses for
    # cache-keying and adapter dispatch, INCLUDING any query+field.
    full_path = path_unescaped
    if query is not None:
        full_path += "?" + _decode_query(query)
    if field_proj is not None:
        full_path += "#" + field_proj.replace("##", "#")

    return SecretRef(
        scheme=scheme,
        path=full_path,
        default=default,
        origin_source=origin_source,
    )


def _split_default(inner: str) -> tuple[str, str | None]:
    """Split ``...:-default`` tail honouring the ``::-`` escape.

    Returns (head, default-or-None).
    """
    # Walk the string finding the first unescaped ":-" boundary.
    i = 0
    n = len(inner)
    while i < n - 1:
        if inner[i] == ":" and inner[i + 1] == "-":
            # Could be the default separator. Check if it's escaped
            # — the escape "::-" means a literal ":-": that requires
            # the character at i-1 to be ":" AND the pair (i-1,i) to
            # not itself be the start of another "::-". We resolve by
            # looking back: if the char immediately before is ":" and
            # we have not already consumed it as part of an earlier
            # "::-", treat it as escape.
            if i > 0 and inner[i - 1] == ":":
                # ":-" preceded by ":" → "::-" escape. Consume past.
                i += 2
                continue
            return inner[:i], inner[i + 2 :]
        i += 1
    return inner, None


def _split_field(s: str) -> tuple[str, str | None]:
    """Split ``...#field`` projection honouring the ``##`` escape.

    Returns (head, field-or-None). `field` is returned with ``##`` left
    intact — caller un-escapes after parse.
    """
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "#":
            if i + 1 < n and s[i + 1] == "#":
                # "##" escape — consume past.
                i += 2
                continue
            return s[:i], s[i + 1 :]
        i += 1
    return s, None


def _split_query(s: str) -> tuple[str, str | None]:
    """Split ``...?query`` honouring the ``??`` escape.

    Returns (head, query-or-None). `query` is returned raw (still
    percent-encoded; caller decodes).
    """
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "?":
            if i + 1 < n and s[i + 1] == "?":
                # "??" escape — consume past.
                i += 2
                continue
            return s[:i], s[i + 1 :]
        i += 1
    return s, None


def _decode_query(query: str) -> str:
    """Decode percent-encoded query string per RFC 3986.

    Returns the canonical "key=value&key=value" form with values
    un-percent-encoded. The result is what gets stored on
    `SecretRef.path` — adapters parse keys/values themselves.
    """
    parts: list[str] = []
    for kv in query.split("&"):
        if "=" not in kv:
            # Spec grammar requires `key=value`; reject malformed input.
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.PARSE_ERROR,
                details=(
                    f"secret reference query parameter '{kv}' is missing '=' "
                    f"(grammar: query_kv := query_key '=' query_value)"
                ),
            )
        key, value = kv.split("=", 1)
        parts.append(f"{key}={unquote(value)}")
    return "&".join(parts)


def walk_secret_refs(tree: Any, *, source_id: str) -> Any:
    """Walk a freshly-loaded tree, converting `${secret:...}` strings to `SecretRef`.

    Called by each ConfigSource immediately after YAML/JSON parse. The
    Phase 1 raw-text interpolator already left `${secret:...}` tokens
    intact (`interpolation._resolve_expr` re-emits them verbatim), so
    this walker sees them as plain strings in scalar leaves.

    Behaviour:
    - String leaf containing exactly one ``${secret:...}`` token and
      nothing else → replaced with a `SecretRef`.
    - String leaf containing the token alongside other text → raises
      `ConfigError(PARSE_ERROR)` — splicing a secret into surrounding
      text is ambiguous and not supported in Phase 2 (scalar fields
      only). Operators wanting interpolated strings should compose at
      the application level.
    - String leaf with no token → unchanged.
    - Mappings and lists → recursed.

    Args:
        tree: The parsed tree (dict / list / scalar nest).
        source_id: ConfigSource.id of the file that produced this tree;
            stored on each emitted SecretRef as `origin_source` for
            diagnostics.

    Returns:
        A new tree with SecretRef placeholders inserted (input unchanged).
    """
    return _walk(tree, source_id)


def _walk(value: Any, source_id: str) -> Any:
    if isinstance(value, dict):
        return {k: _walk(v, source_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, source_id) for v in value]
    if isinstance(value, str):
        return _convert_string(value, source_id)
    return value


def _convert_string(s: str, source_id: str) -> Any:
    """Replace a string containing ``${secret:...}`` with a `SecretRef`.

    Returns either the original string (no token), a `SecretRef` (the
    string IS exactly one token), or raises (string has token mixed
    with other text).
    """
    matches = list(SECRET_REF_OUTER.finditer(s))
    if not matches:
        return s
    if len(matches) == 1:
        m = matches[0]
        if m.start() == 0 and m.end() == len(s):
            return parse_secret_ref(m.group(1), origin_source=source_id)
    # Either multiple tokens, or one token mixed with surrounding text.
    raise ConfigError(
        path="",
        reason=ConfigErrorReason.PARSE_ERROR,
        details=(
            f"a ${{secret:...}} reference must occupy the whole scalar value; "
            f"mixing it with other text is not supported (compose secrets at "
            f"the application level instead): {s!r}"
        ),
        source_id=source_id,
    )
