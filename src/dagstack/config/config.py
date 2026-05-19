"""Config class — primary user-facing API.

Per spec ADR-0001 §4: getters + typed section access + subscriptions.

The Python binding uses snake_case (per PEP 8) for method names — the
spec allows idiomatic naming per binding (§4 "Bindings implement
idiomatically"):

    Python snake_case         Spec abstract name
    ─────────────────────────────────────────────
    has(path)                  has(path)
    get(path, default)         get(path)
    get_string(path, default)  getString(path, default)
    get_int(path, default)     getInt(path, default)
    get_number(path, default)  getNumber(path, default)
    get_bool(path, default)    getBool(path, default)
    get_list(path)             getList(path)
    get_section(path, Schema)  getSection(path, schema)
    load(path)                 Config.load(path)
    load_paths([paths])        Config.load(paths)
    load_from([sources])       Config.loadFrom(sources)
    on_change(path, cb)        Config.onChange(path, callback)
    on_section_change(...)     Config.onSectionChange(path, schema, callback)
    reload()                   Config.reload()
"""

from __future__ import annotations

import os
import re
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

from pydantic import BaseModel, ValidationError

from dagstack.config._constants import IJSON_SAFE_MAX
from dagstack.config.errors import ConfigError, ConfigErrorReason
from dagstack.config.merge import deep_merge_all
from dagstack.config.paths import navigate
from dagstack.config.secrets import (
    EnvSecretSource,
    ResolveContext,
    SecretRef,
    SecretSource,
    SecretValue,
)
from dagstack.config.secrets_mask import MASKED_PLACEHOLDER, is_secret_field
from dagstack.config.sources import ConfigSource, YamlFileSource
from dagstack.config.subscription import (
    Subscription,
    emit_subscription_without_watch_warning,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from dagstack.config.merge import ConfigTree

_ModelT = TypeVar("_ModelT", bound=BaseModel)

# Sentinel for "default not provided" (to distinguish from `default=None`
# as a legitimate default). `None` is a valid config value, not a sentinel.
_MISSING: Any = object()

_INT_RE = re.compile(r"^-?\d+$")
_TRUE_STRINGS = frozenset({"true", "yes", "1"})
_FALSE_STRINGS = frozenset({"false", "no", "0"})


def _is_expired(expires_at: datetime | None) -> bool:
    """ADR-0002 §3 cache rule: a SecretValue with `expires_at` in the
    past is a cache miss. `expires_at=None` means cache for the
    `Config` lifetime.
    """
    if expires_at is None:
        return False
    return datetime.now(tz=UTC) >= expires_at


def _join_pydantic_loc(section: str, loc: tuple[Any, ...]) -> str:
    """Build the full dot-notation path from a section prefix and pydantic loc.

    ADR-0001 v2.2 §4.2 / §4.5: integer segments (array indices) are wrapped
    in `[N]`, not joined with a dot. Example: loc=("servers", 0, "port") +
    section="db" → "db.servers[0].port".
    """
    if not loc:
        return section
    parts: list[str] = [section] if section else []
    for seg in loc:
        if isinstance(seg, int):
            # Array index — attached without a leading dot.
            if parts:
                parts[-1] = f"{parts[-1]}[{seg}]"
            else:
                parts.append(f"[{seg}]")
        else:
            parts.append(str(seg))
    # Join with dots; array tails have already been inlined onto their
    # preceding segment.
    return ".".join(parts)


class Config:
    """Merged config, loaded from one or more sources.

    Usage:
        >>> config = Config.load("app-config.yaml")
        >>> config.get_string("database.host")
        'localhost'
        >>> config.get_int("database.pool_size", default=10)
        10
        >>> db = config.get_section("database", DatabaseConfig)  # Pydantic-validated

    Lifecycle:
    - Config is immutable through the API (getters are read-only).
    - Runtime reconfigure via `reload()` / `on_change` — in Phase 1 a no-op
      with an inactive warning (no watch-capable sources).
    """

    _ENV_VAR_NAME: ClassVar[str] = "DAGSTACK_ENV"

    _tree: ConfigTree
    _source_ids: list[str]
    _secret_sources: dict[str, SecretSource]
    _secret_cache: dict[str, SecretValue]
    _secret_cache_lock: threading.Lock

    def __init__(
        self,
        tree: ConfigTree,
        *,
        source_ids: list[str] | None = None,
        secret_sources: dict[str, SecretSource] | None = None,
    ) -> None:
        self._tree = tree
        self._source_ids = source_ids or []
        self._secret_sources = secret_sources or {}
        self._secret_cache = {}
        self._secret_cache_lock = threading.Lock()

    # ─── Constructors ────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str | Path) -> Config:
        """Load the base config with auto-discovery of layered files.

        Auto-discovery:
        1. `<base>` (required)
        2. `<stem>.local<suffix>` (optional, gitignored)
        3. `<stem>.${DAGSTACK_ENV}<suffix>` (optional)

        Example:
            >>> # $ DAGSTACK_ENV=production
            >>> cfg = Config.load("app-config.yaml")
            # → loads app-config.yaml + app-config.local.yaml? + app-config.production.yaml?
        """
        from pathlib import Path as _Path

        base_path = _Path(path)
        sources: list[ConfigSource] = [YamlFileSource(base_path)]

        local_path = base_path.with_name(f"{base_path.stem}.local{base_path.suffix}")
        if local_path.exists():
            sources.append(YamlFileSource(local_path))

        env_name = os.environ.get(cls._ENV_VAR_NAME)
        if env_name:
            env_path = base_path.with_name(f"{base_path.stem}.{env_name}{base_path.suffix}")
            if env_path.exists():
                sources.append(YamlFileSource(env_path))

        return cls.load_from(sources)

    @classmethod
    def load_paths(cls, paths: Sequence[str | Path]) -> Config:
        """Explicit file layering — order = priority (lowest first)."""
        sources: list[ConfigSource] = [YamlFileSource(p) for p in paths]
        return cls.load_from(sources)

    @classmethod
    def load_from(
        cls,
        sources: Sequence[ConfigSource | SecretSource],
        *,
        eager_secrets: bool = False,
    ) -> Config:
        """Load config from arbitrary sources (ADR-0001 §8 + ADR-0002 §4).

        Accepts a heterogeneous list of `ConfigSource` (provides the tree)
        and `SecretSource` (resolves `${secret:<scheme>:...}` references
        on demand). Dispatch happens by interface; `ConfigSource` order
        defines merge priority (last wins), `SecretSource` order does not
        — each scheme has at most one registered source.

        If no `SecretSource` is passed, an `EnvSecretSource` is auto-
        registered for the `env` scheme. This guarantees
        `${secret:env:VAR}` works without ceremony and matches the
        backwards-compat property of ADR-0002 §1.1.

        After merging, a tree walk validates that every `${secret:...}`
        reference targets a registered scheme (or has a `:-default`).
        References to unknown schemes without a default fail fast at
        load time per ADR-0002 §4 rule 3 — surfacing misconfiguration
        at startup rather than at first request.

        Args:
            sources: Heterogeneous list of `ConfigSource` and
                `SecretSource` instances.
            eager_secrets: If True, walk the merged tree at load time
                and resolve every `SecretRef` up-front (ADR-0002 §3
                "Resolution timing"). Recommended for long-lived
                servers where startup-time errors are preferable to
                first-request errors. Defaults to False (lazy
                resolution at first `get*` call) per the Python
                binding's choice in §3.

        Raises:
            ConfigError(reason=VALIDATION_FAILED): two `SecretSource`
                instances share the same `scheme`.
            ConfigError(reason=SECRET_UNRESOLVED): the merged tree
                contains a `${secret:<scheme>:...}` reference whose
                `<scheme>` is not registered, AND the reference has no
                `:-default` fallback. With `eager_secrets=True`, the
                same reason is also raised when an otherwise-valid
                reference fails resolution at the backend.
        """
        config_sources: list[ConfigSource] = []
        secret_sources: dict[str, SecretSource] = {}
        for src in sources:
            if isinstance(src, SecretSource):
                if src.scheme in secret_sources:
                    raise ConfigError(
                        path="",
                        reason=ConfigErrorReason.VALIDATION_FAILED,
                        details=(
                            f"duplicate SecretSource scheme: {src.scheme!r} "
                            f"(already registered: {secret_sources[src.scheme].id!r}, "
                            f"now adding: {src.id!r})"
                        ),
                    )
                secret_sources[src.scheme] = src
            else:
                config_sources.append(src)

        if "env" not in secret_sources:
            secret_sources["env"] = EnvSecretSource()

        trees: list[ConfigTree] = [source.load() for source in config_sources]
        merged = deep_merge_all(trees) if trees else {}

        # ADR-0002 §4 rule 3: unknown scheme MUST be detected at load
        # time, not at first read. Walk the merged tree, collect every
        # SecretRef, raise on the first reference whose scheme is not
        # registered AND has no default (a default makes the reference
        # safely resolvable even without a backend).
        cls._validate_secret_schemes(merged, secret_sources)

        instance = cls(
            merged,
            source_ids=[source.id for source in config_sources],
            secret_sources=secret_sources,
        )

        if eager_secrets:
            instance._eager_resolve_all()

        return instance

    @staticmethod
    def _validate_secret_schemes(tree: Any, secret_sources: dict[str, SecretSource]) -> None:
        """Walk the merged tree; raise on the first unknown-scheme reference."""
        if isinstance(tree, dict):
            for value in tree.values():
                Config._validate_secret_schemes(value, secret_sources)
        elif isinstance(tree, list):
            for value in tree:
                Config._validate_secret_schemes(value, secret_sources)
        elif (
            isinstance(tree, SecretRef)
            and tree.scheme not in secret_sources
            and tree.default is None
        ):
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_UNRESOLVED,
                details=(
                    f"no SecretSource registered for scheme {tree.scheme!r} "
                    f"(referenced from {tree.origin_source!r}); "
                    f"available schemes: {sorted(secret_sources)}"
                ),
            )

    # ─── Introspection ───────────────────────────────────────────────────────

    def source_ids(self) -> list[str]:
        """Source ids that this Config was built from. For diagnostics / logging.

        ADR-0001 v2.1 §4.1: a method, not a property — for cross-binding
        parity with TS `sourceIds()` and Go `SourceIDs()`. The value is
        computed from the current list of sources; a copy is returned so
        the caller cannot mutate internal state.
        """
        return list(self._source_ids)

    def has(self, path: str) -> bool:
        """True if the key/index at path exists in the merged tree."""
        try:
            navigate(self._tree, path)
        except ConfigError as err:
            if err.reason in {ConfigErrorReason.MISSING, ConfigErrorReason.TYPE_MISMATCH}:
                return False
            raise
        return True

    # ─── Secret resolution (ADR-0002 §3) ─────────────────────────────────────

    def _resolve_secret_ref(self, ref: SecretRef, *, path: str) -> str:
        """Resolve a `SecretRef` placeholder via its registered SecretSource.

        Cached for the `Config` lifetime, keyed by ``<scheme>:<full-path>``
        so identical references share resolution. Adapters that fetch
        an envelope from a backend may keep their own internal cache
        if two references with different `#field` projections target
        the same backend secret (e.g. `VaultSource`).

        Args:
            ref: The placeholder produced by the file-source walker.
            path: Dot-notation path of the leaf, used for diagnostic
                attribution if resolution fails.

        Returns:
            The resolved string value, with any ``#field`` projection
            and ``?query`` already applied by the adapter. Type coercion
            happens at the `get*` call site.

        Raises:
            ConfigError(reason=SECRET_UNRESOLVED): no source registered
                for `ref.scheme`, key not found, or the source raised
                and the reference has no default.
        """
        cache_key = f"{ref.scheme}:{ref.path}"

        cached = self._secret_cache.get(cache_key)
        if cached is not None and not _is_expired(cached.expires_at):
            return cached.value

        source = self._secret_sources.get(ref.scheme)
        if source is None:
            if ref.default is not None:
                return ref.default
            raise ConfigError(
                path=path,
                reason=ConfigErrorReason.SECRET_UNRESOLVED,
                details=(
                    f"no SecretSource registered for scheme {ref.scheme!r} "
                    f"(referenced from {ref.origin_source!r})"
                ),
            )

        # Coalesce concurrent first-touch under a single lock — the
        # cache-hit fast path above stays lock-free. Without this two
        # threads racing on the same cold key would issue two backend
        # round-trips (per ADR-0002 §Open-questions 3 RECOMMENDED).
        with self._secret_cache_lock:
            cached = self._secret_cache.get(cache_key)
            if cached is not None and not _is_expired(cached.expires_at):
                return cached.value
            try:
                # The adapter receives the full path (including
                # `?query` and `#field`); it owns parsing and any
                # projection.
                resolved = source.resolve(ref.path, ResolveContext())
            except ConfigError:
                if ref.default is not None:
                    return ref.default
                raise

            self._secret_cache[cache_key] = resolved
            return resolved.value

    def refresh_secrets(self) -> None:
        """Drop the resolved-secrets cache (ADR-0002 §3 "Forced refresh").

        Subsequent `get*` calls re-resolve every `${secret:...}`
        reference against its registered `SecretSource`. This is the
        Phase 2 manual-rotation hook; push-based rotation is deferred
        to Phase 3.

        Safe to call from any thread. In-flight `get*` calls observe
        either the previous or the next resolved value, never a torn
        intermediate.
        """
        with self._secret_cache_lock:
            self._secret_cache.clear()

    def snapshot(self, *, include_secrets: bool = False) -> Any:
        """Return a copy of the merged tree with secrets masked
        (ADR-0002 §3 "Resolution timing" trigger table).

        Default behaviour: every `SecretRef` placeholder is replaced
        with `[MASKED]`. The reference itself is NOT resolved — no
        backend round-trip happens. Field-name-based masking from
        `_meta/secret_patterns.yaml` is also applied for plain string
        values whose key matches a secret pattern (e.g. `api_key`).

        With `include_secrets=True`, `SecretRef` placeholders ARE
        resolved (audit-mode opt-in), then field-name suffix masking
        runs over the resolved tree. Callers MUST treat the returned
        object as sensitive.

        The returned object is a deep copy — mutating it does not
        affect subsequent `get*` calls.
        """
        return self._snapshot_walk(self._tree, include_secrets=include_secrets)

    def _snapshot_walk(self, value: Any, *, include_secrets: bool, key: str = "") -> Any:
        if isinstance(value, dict):
            return {
                k: self._snapshot_walk(v, include_secrets=include_secrets, key=k)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self._snapshot_walk(v, include_secrets=include_secrets, key=key) for v in value]
        if isinstance(value, SecretRef):
            if not include_secrets:
                return MASKED_PLACEHOLDER
            try:
                resolved = self._resolve_secret_ref(value, path=key)
            except ConfigError:
                return MASKED_PLACEHOLDER
            return MASKED_PLACEHOLDER if is_secret_field(key) else resolved
        if isinstance(value, str) and is_secret_field(key) and value:
            return MASKED_PLACEHOLDER
        return value

    def _eager_resolve_all(self) -> None:
        """Walk the merged tree and pre-populate `_secret_cache` for every
        `SecretRef`. Surfaces backend errors at load time rather than at
        first read. Used by `load_from(..., eager_secrets=True)`.
        """

        def walk(value: Any, path: str = "") -> None:
            if isinstance(value, dict):
                for k, v in value.items():
                    walk(v, f"{path}.{k}" if path else k)
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    walk(v, f"{path}[{i}]")
            elif isinstance(value, SecretRef):
                self._resolve_secret_ref(value, path=path)

        walk(self._tree)

    def _maybe_resolve(self, value: Any, *, path: str) -> Any:
        """Convert a `SecretRef` leaf to the resolved string; pass-through otherwise."""
        if isinstance(value, SecretRef):
            return self._resolve_secret_ref(value, path=path)
        return value

    # ─── Getters ─────────────────────────────────────────────────────────────

    def get(self, path: str, default: Any = _MISSING) -> Any:
        """Return the raw value at path (no type coercion).

        `${secret:...}` references are resolved transparently via the
        registered SecretSource (ADR-0002 §3) before return — callers
        do not see `SecretRef` placeholders.
        """
        try:
            return self._maybe_resolve(navigate(self._tree, path), path=path)
        except ConfigError as err:
            if default is not _MISSING and err.reason is ConfigErrorReason.MISSING:
                return default
            raise

    def get_string(self, path: str, default: Any = _MISSING) -> str:
        """String value at path — strict (v2.1 §4.3, no implicit coercion).

        **Breaking change v0.2.0**: prior to v0.1.0 `get_string` coerced
        `int`/`float`/`bool` to `str` (`42 → "42"`, `True → "true"`).
        Per ADR v2.1 §4.3 getters are strict; coercion was removed for
        parity with the Go binding and wire stability.

        For an explicit conversion use `str(cfg.get(path))` or one of the
        specialized getters (`get_int`, `get_bool`, `get_number`).
        """
        try:
            value = self._maybe_resolve(navigate(self._tree, path), path=path)
        except ConfigError as err:
            if default is not _MISSING and err.reason is ConfigErrorReason.MISSING:
                return default  # type: ignore[no-any-return]
            raise
        if isinstance(value, str):
            return value
        raise ConfigError(
            path=path,
            reason=ConfigErrorReason.TYPE_MISMATCH,
            details=f"expected string, got {type(value).__name__} (v2.1 §4.3 strict)",
        )

    def get_int(self, path: str, default: Any = _MISSING) -> int:
        r"""Integer value at path.

        Accepts:
        - `int` (but not `bool` — a separate type per v2.1 §4.3).
        - String matching ``^-?\d+$`` — coerced to int.
        - Whole-number `float` in the i-JSON safe range (`±(2^53-1)`) —
          for JSON sources where integer-like literals deserialize as
          float (v2.1 §4.3 clarification).

        Float with a fractional part or outside the safe range →
        TYPE_MISMATCH.
        """
        try:
            value = self._maybe_resolve(navigate(self._tree, path), path=path)
        except ConfigError as err:
            if default is not _MISSING and err.reason is ConfigErrorReason.MISSING:
                return default  # type: ignore[no-any-return]
            raise
        if isinstance(value, bool):
            # Bool is a subclass of int; do not coerce to int (strict coercion per spec).
            raise ConfigError(
                path=path,
                reason=ConfigErrorReason.TYPE_MISMATCH,
                details="expected int, got bool (strict coercion — bool not int)",
            )
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if value.is_integer() and abs(value) <= IJSON_SAFE_MAX:
                return int(value)
            raise ConfigError(
                path=path,
                reason=ConfigErrorReason.TYPE_MISMATCH,
                details=(
                    f"expected int, got float {value!r} "
                    "(fractional or outside i-JSON safe range ±(2^53-1))"
                ),
            )
        if isinstance(value, str) and _INT_RE.match(value):
            return int(value)
        raise ConfigError(
            path=path,
            reason=ConfigErrorReason.TYPE_MISMATCH,
            details=f"expected int, got {type(value).__name__}",
        )

    def get_number(self, path: str, default: Any = _MISSING) -> float:
        """Float value at path. Int / integer-string / float-string are coerced."""
        try:
            value = self._maybe_resolve(navigate(self._tree, path), path=path)
        except ConfigError as err:
            if default is not _MISSING and err.reason is ConfigErrorReason.MISSING:
                return default  # type: ignore[no-any-return]
            raise
        if isinstance(value, bool):
            raise ConfigError(
                path=path,
                reason=ConfigErrorReason.TYPE_MISMATCH,
                details="expected number, got bool",
            )
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError as err:
                raise ConfigError(
                    path=path,
                    reason=ConfigErrorReason.TYPE_MISMATCH,
                    details=f"string {value!r} cannot be parsed as number",
                ) from err
        raise ConfigError(
            path=path,
            reason=ConfigErrorReason.TYPE_MISMATCH,
            details=f"expected number, got {type(value).__name__}",
        )

    def get_bool(self, path: str, default: Any = _MISSING) -> bool:
        """Bool value at path.

        Strict coercion per spec §4.3: only the strings
        `true|false|yes|no|1|0` (case-insensitive). Other strings /
        numbers → TYPE_MISMATCH.
        """
        try:
            value = self._maybe_resolve(navigate(self._tree, path), path=path)
        except ConfigError as err:
            if default is not _MISSING and err.reason is ConfigErrorReason.MISSING:
                return default  # type: ignore[no-any-return]
            raise
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.lower()
            if lowered in _TRUE_STRINGS:
                return True
            if lowered in _FALSE_STRINGS:
                return False
            raise ConfigError(
                path=path,
                reason=ConfigErrorReason.TYPE_MISMATCH,
                details=(
                    f"string {value!r} cannot be parsed as bool "
                    "(expected one of true/false/yes/no/1/0, case-insensitive)"
                ),
            )
        raise ConfigError(
            path=path,
            reason=ConfigErrorReason.TYPE_MISMATCH,
            details=f"expected bool, got {type(value).__name__}",
        )

    def get_list(self, path: str) -> list[Any]:
        """List value at path. No default — a list is required when accessed via getList."""
        value = self.get(path)
        if not isinstance(value, list):
            raise ConfigError(
                path=path,
                reason=ConfigErrorReason.TYPE_MISMATCH,
                details=f"expected array (list), got {type(value).__name__}",
            )
        # Return a copy — the caller can mutate it without affecting the internal tree.
        return list(value)

    def get_section(self, path: str, schema: type[_ModelT]) -> _ModelT:
        """Typed section access via a Pydantic BaseModel.

        ADR-0001 v2.1 §4.4 Typed section access: env-substituted strings
        are coerced into schema fields according to `_meta/coercion.yaml`
        (Pydantic does this automatically for int/float/bool from strings
        matching the corresponding regex). The reverse case — a native
        int/float/bool in a string field — is rejected with
        reason=TYPE_MISMATCH (mirroring §4.3 getString strict mode).

        v2.1 §4.5 Path preservation: for a nested validation failure
        path = `<section>.<field>` (not just `<section>`).

        Args:
            path: Dot-notation path to the section (a map in the merged tree).
            schema: Pydantic BaseModel subclass describing the expected shape.

        Returns:
            Validated Pydantic instance.

        Raises:
            ConfigError(MISSING): path does not exist.
            ConfigError(TYPE_MISMATCH): value is not a map, or a native
                int/float/bool was provided for a string field of the schema.
            ConfigError(VALIDATION_FAILED): Pydantic validation failed for
                other reasons (range / pattern / enum / etc.).
        """
        data = self.get(path)
        if not isinstance(data, dict):
            raise ConfigError(
                path=path,
                reason=ConfigErrorReason.TYPE_MISMATCH,
                details=f"expected object (map) for section, got {type(data).__name__}",
            )
        try:
            return schema.model_validate(data)
        except ValidationError as err:
            first = err.errors()[0]
            # Full dot-notation path: section + field path.
            # ADR-0001 v2.2 §4.2 / §4.5: array indices → [N], not .N.
            full_path = _join_pydantic_loc(path, first.get("loc", ()))

            # ADR-0001 v2.2 §6 Secrets masking: if the failed field is a
            # secret, mask its value in details. Pydantic typically
            # includes `input_value` in the error; if so, we substitute it.
            field_name = str(first["loc"][-1]) if first.get("loc") else ""
            details_msg = f"Pydantic validation failed: {err}"
            if is_secret_field(field_name):
                input_val = first.get("input")
                if input_val not in (None, ""):
                    details_msg = details_msg.replace(
                        repr(input_val), repr(MASKED_PLACEHOLDER)
                    ).replace(str(input_val), MASKED_PLACEHOLDER)

            # Reverse-coerce check (spec §4.4 M1): a native non-string
            # scalar in a string field is type_mismatch, not
            # validation_failed.
            reason = ConfigErrorReason.VALIDATION_FAILED
            if first.get("type") == "string_type":
                actual_input = first.get("input")
                if isinstance(actual_input, (int, float, bool)):
                    reason = ConfigErrorReason.TYPE_MISMATCH

            raise ConfigError(
                path=full_path,
                reason=reason,
                details=details_msg,
            ) from err

    # ─── Subscriptions (Phase 1: inactive) ────────────────────────────────────

    def on_change(
        self,
        path: str,
        callback: Callable[[Any], None],
    ) -> Subscription:
        """Subscribe to value changes at path.

        Phase 1: the callback never fires (no watch-capable sources).
        Returns a `Subscription` with `active=False` and a diagnostic warning.
        """
        return self._build_inactive_subscription(path)

    def on_section_change(
        self,
        path: str,
        schema: type[BaseModel],
        callback: Callable[[BaseModel, BaseModel], None],
    ) -> Subscription:
        """Typed subscription to section changes.

        Phase 1: inactive (see `on_change`).
        """
        return self._build_inactive_subscription(path)

    def reload(self) -> None:
        """Trigger an explicit reload of all sources.

        Phase 1: a no-op for every source (none support push events). The
        method is reserved for the Phase 2+ admin API.
        """
        # Phase 2+: iterate sources, collect new trees, re-merge, validate, swap.

    # ─── Internal helpers ────────────────────────────────────────────────────

    def _build_inactive_subscription(self, path: str) -> Subscription:
        """Build a Subscription with active=false and emit a subscription_without_watch warning."""
        emit_subscription_without_watch_warning(
            path=path,
            source_ids=list(self._source_ids),
        )
        return Subscription(
            path=path,
            active=False,
            inactive_reason="no watch-capable source registered",
        )
