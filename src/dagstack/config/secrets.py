"""Phase 2 secret references and SecretSource adapters.

Implements the `SecretSource` contract from ADR-0002 §2 plus the
mandatory in-process `EnvSecretSource` that resolves
``${secret:env:VAR}`` against ``os.environ``.

Public API (re-exported from `dagstack.config`):

    from dagstack.config import (
        SecretSource, AsyncSecretSource,
        SecretRef, SecretValue, ResolveContext,
        EnvSecretSource,
    )

`VaultSource` lives in `dagstack.config.vault` (per ADR-0002 §6.5)
and ships as the ``[vault]`` extra
(`pip install dagstack-config[vault]`); see `adr/0001-vault-source.md`
for the SDK choice rationale.

Resolution timing — see ADR-0002 §3:
- File sources emit `SecretRef` placeholders at `Source.load()` time;
  no secret-manager round-trip happens during `Config.load_from()`.
- The placeholder is resolved transparently at the first
  `config.get_string(...)` (or any other `get*`) call on that path.
- `Config.load_from(..., eager_secrets=True)` walks the merged tree
  at load time and resolves every placeholder up-front — recommended
  for long-lived servers where startup-time errors are preferable to
  first-request errors.

Caching — see ADR-0002 §3:
- Resolved values are cached for the `Config` lifetime, keyed by
  ``<scheme>:<full-ref-path>`` (including any ``?query`` and
  ``#field``). Adapters that fetch a multi-key envelope from a
  backend are free to add an internal cache so two references with
  different ``#field`` projections of the same backend secret share
  one round-trip — that secondary cache is the adapter's concern,
  not the loader's.
- `SecretValue.expires_at` is honoured by the cache: a value with
  `expires_at` in the past is treated as a cache miss and triggers
  re-resolution on the next read (ADR-0002 §3 cache rule). Proactive
  TTL-driven invalidation (push-based rotation events) is a Phase 3
  candidate.
- `Config.refresh_secrets()` drops the cache and forces re-resolution
  on next access — the manual rotation hook for Phase 2 (ADR-0002 §3
  "Forced refresh"). Push-based rotation (Vault lease watcher,
  AWS-SM rotation event subscription) lands in Phase 3.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from dagstack.config.errors import ConfigError, ConfigErrorReason

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

__all__ = [
    "AsyncSecretSource",
    "EnvSecretSource",
    "ResolveContext",
    "SecretRef",
    "SecretSource",
    "SecretValue",
]


# ── Value types ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SecretRef:
    """Opaque placeholder for an unresolved ``${secret:...}`` reference.

    Lives in the merged config tree mixed with regular scalars after
    `Config.load_from(...)`. Resolved transparently by `get*` methods
    via the `SecretSource` registered for `scheme`.

    Equality: two `SecretRef` instances are equal iff their `scheme`,
    `path`, and `default` match. The `origin_source` is diagnostic-only
    and does not participate in equality.
    """

    scheme: str
    path: str
    default: str | None = None
    origin_source: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class SecretValue:
    """The result of `SecretSource.resolve(path, ctx)`.

    `value` is always a string at the wire level — type coercion happens
    at the `Config.get*` call site, exactly like for env-interpolated
    values (ADR-0001 §4.4). The binding MUST NOT JSON-parse the value
    into a sub-tree; sub-key projection is handled at the
    ``<scheme>:<path>#<field>`` parse step before the adapter runs
    (ADR-0002 §1.2).
    """

    value: str
    source_id: str
    version: str | None = None
    expires_at: datetime | None = None


@dataclass(slots=True)
class ResolveContext:
    """Per-call context object passed to `SecretSource.resolve`.

    `cancellation` is a binding-native, opaque handle — typically an
    `asyncio.Task`, an `anyio.CancelScope`, an `asyncio.Event`, or
    `None` for sync callers without cancellation. Adapters MAY inspect
    its concrete type; the loader passes it through untouched.
    `attempt` is 1-based and incremented monotonically by the loader on
    retry. Adapters MAY read it to pick a longer per-attempt timeout;
    the loader itself does not implement automatic retries (ADR-0002
    §Open-questions 4).
    """

    attempt: int = 1
    deadline: datetime | None = None
    cancellation: Any = None  # binding-native handle; typed as Any per docstring


# ── Protocols ──────────────────────────────────────────────────────────


@runtime_checkable
class SecretSource(Protocol):
    """Adapter contract per ADR-0002 §2.

    Required:
        scheme: short scheme name; matches the leading token in
            ``${secret:<scheme>:...}``. Lowercase ASCII; the loader
            uses it as a registry key.
        id: human-readable identifier (URI-style by convention,
            e.g., ``"vault:https://vault.example.com"``). Carried in
            `SecretValue.source_id` and in `ConfigError.source_id` for
            diagnostics.
        resolve: synchronous resolver. Bindings choose sync or async
            per ADR-0001 §4 — Python ships both `SecretSource`
            (sync `def resolve`) and `AsyncSecretSource`
            (async `def resolve_async`).

    Optional:
        close: release resources (HTTP pool, file handle, lease
            renewal task). The loader calls `close()` on every
            registered source when `Config.close()` is called.
    """

    @property
    def scheme(self) -> str: ...

    @property
    def id(self) -> str: ...

    def resolve(self, path: str, ctx: ResolveContext) -> SecretValue: ...

    def close(self) -> None: ...


@runtime_checkable
class AsyncSecretSource(Protocol):
    """Async-flavoured parallel protocol for non-blocking event loops.

    Distinct type from `SecretSource` so callers get a clear naming
    boundary — a sync caller cannot accidentally `await` a sync
    `resolve` (mypy flags it), and a separate `resolve_async` name
    makes the wrong call a discoverable grep target rather than a
    runtime trap (Python's type system itself does not prevent a
    sync call from inside an async function — naming discipline does).

    A single adapter MAY implement both protocols (and the Vault
    pilot will, once an async hvac path lands); the loader picks the
    right method based on the calling context.
    """

    @property
    def scheme(self) -> str: ...

    @property
    def id(self) -> str: ...

    async def resolve_async(self, path: str, ctx: ResolveContext) -> SecretValue: ...

    async def close_async(self) -> None: ...


# ── Built-in EnvSecretSource ───────────────────────────────────────────


class EnvSecretSource:
    """In-process SecretSource for the mandatory ``env`` scheme.

    `${secret:env:VAR}` is semantically identical to `${VAR}` from
    ADR-0001 §2 — the env scheme is a degenerate case of secret
    resolution. Auto-registered by the loader if the consumer does not
    pass one explicitly (ADR-0002 §4 rule 2).

    The path is the env-var name (`OPENAI_API_KEY`, `DATABASE_URL`).
    Default values from the reference (`${secret:env:VAR:-fallback}`)
    are handled by the loader before `resolve()` is called — the
    adapter only sees `path`, never the `default`.
    """

    scheme: str = "env"

    def __init__(
        self,
        *,
        getenv: Callable[[str], str | None] | None = None,
    ) -> None:
        """Build an EnvSecretSource.

        Args:
            getenv: Optional callable replacing `os.environ.get` for
                tests. Defaults to `os.environ.get`.
        """
        self._getenv: Callable[[str], str | None] = getenv if getenv is not None else os.environ.get
        self._id = "env:os.environ"

    @property
    def id(self) -> str:
        return self._id

    def resolve(self, path: str, ctx: ResolveContext) -> SecretValue:
        del ctx  # env resolution is synchronous and cheap
        # The env scheme operates on env-var names — it does not
        # support `?query` or `#field` projection (env values are
        # opaque single-value strings). If the operator tries to use
        # them, surface that explicitly rather than silently looking
        # up an env var named like `K?version=1#x` (which would fail
        # in a misleading way).
        for sep, hint in (("?", "query parameters"), ("#", "sub-key projection")):
            if sep in path:
                raise ConfigError(
                    path="",
                    reason=ConfigErrorReason.SECRET_UNRESOLVED,
                    details=(
                        f"env scheme does not support {hint} ({sep!r} in {path!r}); "
                        f"env values are opaque single-value strings — switch to "
                        f"a JSON-typed backend such as VaultSource if you need "
                        f"structured secrets."
                    ),
                    source_id=self._id,
                )

        value = self._getenv(path)
        if value is None:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_UNRESOLVED,
                details=(
                    f"env:{path} is not set in the process environment "
                    f"and the reference has no default"
                ),
                source_id=self._id,
            )
        return SecretValue(value=value, source_id=self._id)

    def close(self) -> None:
        """No-op — env source holds no resources."""
