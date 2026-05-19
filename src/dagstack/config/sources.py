"""ConfigSource abstraction + Phase 1 implementations.

Per spec ADR-0001 §8.1: the source contract is `id`, `load()`, the
`interpolate` hint, and optional `watch()` (Phase 2+) and `close()`.
Phase 1 implements three sources: `YamlFileSource`, `JsonFileSource`,
`InMemorySource`.

Sync-only API in Phase 1 — the async idiom is deferred until the first
real watch scenario (OTLP / etcd). Per spec §4 "Sync vs async — binding
decides".
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import yaml

from dagstack.config._constants import IJSON_SAFE_MAX
from dagstack.config.errors import ConfigError, ConfigErrorReason
from dagstack.config.interpolation import interpolate


class Yaml12StrictLoader(yaml.SafeLoader):
    """PyYAML loader configured for YAML 1.2 strict mode.

    ADR-0001 v2.2 §2: bindings MUST parse configs in YAML 1.2, not the
    legacy 1.1. The practical consequence: `yes` / `no` / `on` / `off` /
    `Y` / `N` / `y` / `n` remain strings (not bools). `true` / `false`
    (any case) become native bool. PyYAML by default inherits the YAML
    1.1 BOOL_VALUES resolver; passing `version=(1,2)` to `yaml.load()`
    does not change that. A custom resolver is the working path.
    """


# Clear inherited YAML 1.1 bool resolvers for our loader.
# The original pyyaml `SafeResolver.add_implicit_resolver` registers
# `tag:yaml.org,2002:bool` with a regex matching yes/no/on/off/true/false.
# We strip all bool resolvers and register the YAML 1.2 variant with
# only true/false.
import re  # noqa: E402  — local import for bootstrap resolver

_BOOL_TAG = "tag:yaml.org,2002:bool"
Yaml12StrictLoader.yaml_implicit_resolvers = {
    ch: [(tag, regex) for (tag, regex) in resolvers if tag != _BOOL_TAG]
    for ch, resolvers in Yaml12StrictLoader.yaml_implicit_resolvers.items()
}
Yaml12StrictLoader.add_implicit_resolver(  # type: ignore[no-untyped-call]
    _BOOL_TAG,
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dagstack.config.merge import ConfigTree


@runtime_checkable
class ConfigSource(Protocol):
    """Source contract per spec §8.1.

    Implementations MUST expose:
        id: human-readable identifier (URI-style by convention).
        interpolate: hint to the loader — if True, `${VAR}` in string
            leaves is resolved via env interpolation before the dataset
            is emitted.
        load() -> ConfigTree: returns the parsed tree (may raise ConfigError).

    Optional (Phase 2+):
        watch(callback) -> Subscription — push-based reload.
        close() -> None — release resources.
    """

    id: str
    interpolate: bool

    def load(self) -> ConfigTree: ...


class YamlFileSource:
    """YAML file source with env interpolation.

    Interpolation runs before YAML parsing, on the raw text of the file.
    This allows substituting env vars in non-string YAML positions
    (example: `dimension: ${EMBEDDINGS_DIMENSION:-768}` → after
    interpolation `dimension: 768` → after parse — `int(768)`).
    """

    def __init__(
        self,
        path: str | Path,
        *,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._path = Path(path)
        self._env = env if env is not None else os.environ
        self.id = f"yaml:{self._path}"
        self.interpolate = True

    def load(self) -> ConfigTree:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SOURCE_UNAVAILABLE,
                details=f"file not found: {self._path}",
                source_id=self.id,
            ) from e
        except OSError as e:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SOURCE_UNAVAILABLE,
                details=f"cannot read file {self._path}: {e}",
                source_id=self.id,
            ) from e

        interpolated = interpolate(text, self._env, source_id=self.id)

        try:
            # ADR-0001 v2.2 §2: YAML 1.2 strict mode (yes/no/on/off → strings).
            parsed: Any = yaml.load(interpolated, Loader=Yaml12StrictLoader)
        except yaml.YAMLError as e:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.PARSE_ERROR,
                details=f"YAML parse error in {self._path}: {e}",
                source_id=self.id,
            ) from e

        # ADR-0002 §3: convert any `${secret:...}` strings into SecretRef
        # placeholders. The Phase 1 raw-text interpolator left them
        # intact (interpolation._resolve_expr re-emits them verbatim).
        from dagstack.config._secret_grammar import walk_secret_refs

        coerced = _coerce_root(_normalize_numbers(parsed), self.id)
        walked: ConfigTree = walk_secret_refs(coerced, source_id=self.id)
        return walked


class JsonFileSource:
    """JSON file source — same semantics as YAML (YAML 1.2 is a superset of JSON).

    Used in scenarios where a YAML parser is not available or the input
    is generated by another tool (for example, terraform output or a
    CI-generated config).
    """

    def __init__(
        self,
        path: str | Path,
        *,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._path = Path(path)
        self._env = env if env is not None else os.environ
        self.id = f"json:{self._path}"
        self.interpolate = True

    def load(self) -> ConfigTree:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SOURCE_UNAVAILABLE,
                details=f"file not found: {self._path}",
                source_id=self.id,
            ) from e
        except OSError as e:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SOURCE_UNAVAILABLE,
                details=f"cannot read file {self._path}: {e}",
                source_id=self.id,
            ) from e

        interpolated = interpolate(text, self._env, source_id=self.id)

        try:
            parsed: Any = json.loads(interpolated)
        except json.JSONDecodeError as e:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.PARSE_ERROR,
                details=f"JSON parse error in {self._path}: {e}",
                source_id=self.id,
            ) from e

        # ADR-0002 §3 — same SecretRef walk as YamlFileSource.
        from dagstack.config._secret_grammar import walk_secret_refs

        coerced = _coerce_root(_normalize_numbers(parsed), self.id)
        walked: ConfigTree = walk_secret_refs(coerced, source_id=self.id)
        return walked


class InMemorySource:
    """In-memory source — tests and programmatic bootstrap.

    Interpolation is disabled by default (the tree is already
    structurally correct; `${VAR}` is not processed). Pass
    `interpolate=True` explicitly when needed.
    """

    def __init__(
        self,
        tree: ConfigTree,
        *,
        source_id: str = "in-memory",
        interpolate: bool = False,
    ) -> None:
        self._tree = tree
        self.id = source_id
        self.interpolate = interpolate

    def load(self) -> ConfigTree:
        # Return a shallow copy — the caller must not mutate the
        # original tree through the sink. Deep copying happens in the
        # merge step.
        # ADR-0002 §3 — convert any `${secret:...}` strings into
        # SecretRef placeholders, same as file sources.
        from dagstack.config._secret_grammar import walk_secret_refs

        walked: ConfigTree = walk_secret_refs(dict(self._tree), source_id=self.id)
        return walked


def _coerce_root(parsed: Any, source_id: str) -> ConfigTree:
    """Validate the parsed root as a mapping; empty file → empty dict."""
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ConfigError(
            path="",
            reason=ConfigErrorReason.PARSE_ERROR,
            details=f"root must be a mapping, got {type(parsed).__name__}",
            source_id=source_id,
        )
    return parsed


def _normalize_numbers(obj: Any) -> Any:
    """Whole-number float in safe range → int (source-level normalization).

    Applied to `YamlFileSource` and `JsonFileSource` for tree-level
    consistency:

    - PyYAML `yaml.safe_load('x: 100.0')` → `{'x': 100.0}` (float).
    - `json.loads('{"x": 100.0}')` → `{'x': 100.0}` (float).
    - `yaml.safe_load('x: 100')` → `{'x': 100}` (int).
    - `json.loads('{"x": 100}')` → `{'x': 100}` (int).

    Per v2.1 §4.3 / `_meta/coercion.yaml`, a whole-number float in the
    i-JSON safe range (`±(2^53-1)`) is semantically equivalent to an
    int; normalizing at the load stage means `cfg.get("x")`,
    `get_int("x")`, `get_section(X, schema-with-int-field)` and
    canonical JSON emission all behave the same regardless of whether
    the literal was `100` or `100.0` in the YAML/JSON source. This
    matches `dagstack/config-go` v0.1.0 `JsonFileSource` +
    `YamlFileSource` (Go uses a `yaml.Node.Kind` check).

    Out-of-range whole-number floats remain float (precision is not
    guaranteed); fractional values are unchanged. Bool is unaffected
    (`isinstance(bool, float)` is False).
    """
    if isinstance(obj, dict):
        return {k: _normalize_numbers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_numbers(v) for v in obj]
    if isinstance(obj, float) and obj.is_integer() and abs(obj) <= IJSON_SAFE_MAX:
        return int(obj)
    return obj
