"""Pilot HashiCorp Vault SecretSource adapter (ADR-0002 §6).

Optional extra: install with ``pip install dagstack-config[vault]`` to
pull `hvac` (the canonical Python client). Without the extra, importing
this module raises ``ImportError`` with an actionable hint.

Phase 2 scope (ADR-0002 §6.1 / §6.2):

- KV v2 only. KV v1 lacks versioning and soft-delete; if any operator
  needs it, ships in Phase 3 as ``VaultKvV1Source``.
- Token auth (mandatory) + AppRole auth (mandatory). Kubernetes
  ServiceAccount auth (optional) — included here as ``KubernetesAuth``.
- Namespace support (Vault Enterprise) — pass at construction time.
- ``?version=N`` query — read a specific KV v2 version.
- ``#field`` projection — pluck a sub-key from the JSON-typed secret.

Token self-renewal lands in a follow-up PR alongside
``AsyncSecretSource`` — the renewal timer is a natural fit for the
event loop and is deferred to keep this PR focused on the read path.

Usage::

    from dagstack.config import Config
    from dagstack.config.vault import VaultSource, TokenAuth, AppRoleAuth

    cfg = Config.load_from([
        YamlFileSource("app-config.yaml"),
        VaultSource(
            addr="https://vault.example.com",
            auth=TokenAuth(token=os.environ["VAULT_TOKEN"]),
            namespace="dagstack/prod",
        ),
    ])
    api_key = cfg.get_string("llm.api_key")  # ${secret:vault:secret/dagstack/prod/openai#api_key}
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeAlias
from urllib.parse import parse_qs

from dagstack.config.errors import ConfigError, ConfigErrorReason
from dagstack.config.secrets import ResolveContext, SecretValue

if TYPE_CHECKING:
    from collections.abc import Iterable

try:
    import hvac
    from hvac.exceptions import (
        Forbidden,
        InvalidPath,
        InvalidRequest,
        VaultDown,
        VaultError,
    )
except ImportError as exc:  # pragma: no cover — diagnostic only
    msg = (
        "VaultSource requires the `[vault]` extra. Install with: pip install dagstack-config[vault]"
    )
    raise ImportError(msg) from exc

__all__ = [
    "AppRoleAuth",
    "KubernetesAuth",
    "TokenAuth",
    "VaultAuth",
    "VaultSource",
]


# ── Auth descriptors ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TokenAuth:
    """Direct Vault token. Simplest case; supports any deployment that
    already injects a token via init-container or operator action."""

    token: str


@dataclass(frozen=True, slots=True)
class AppRoleAuth:
    """AppRole authentication — production CI/CD pipeline default."""

    role_id: str
    secret_id: str
    mount_point: str = "approle"


@dataclass(frozen=True, slots=True)
class KubernetesAuth:
    """Kubernetes ServiceAccount authentication. Reads the SA JWT from
    the standard projected-token path; one Vault `auth/kubernetes/login`
    round-trip per VaultSource lifetime (no in-flight renewal in Phase 2)."""

    role: str
    jwt_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    mount_point: str = "kubernetes"


# Discriminated union of supported auth methods. Public type alias —
# operators import `VaultAuth` to type their own factory functions.
# New auth methods (AWS IAM, JWT/OIDC, TLS client cert) land per
# operator demand in Phase 3.
VaultAuth: TypeAlias = TokenAuth | AppRoleAuth | KubernetesAuth


# ── VaultSource ───────────────────────────────────────────────────────


class VaultSource:
    """SecretSource for HashiCorp Vault KV v2 (ADR-0002 §6).

    The `scheme` is hard-coded to ``"vault"``. Operators wanting to
    register two Vault clusters use a custom subclass with overridden
    ``scheme`` (e.g. ``vault-prod`` and ``vault-dr``) — schemes are an
    operator-extensible space per ADR-0002 §Open-questions 1.

    Path layout: the user-visible path is what ``vault kv get`` accepts
    (e.g. ``secret/dagstack/prod/openai``). The first segment is the
    KV v2 mount point (default Vault setup uses ``secret``); the
    remainder is the logical key path. The hvac client handles the
    ``secret/data/...`` HTTP rewrite internally.

    Path also supports the optional ``?version=N`` query (read a
    specific KV v2 version) and the ``#field`` projection (pluck a
    sub-key from a multi-key secret) per ADR-0002 §6.3.
    """

    scheme: str = "vault"

    def __init__(
        self,
        addr: str,
        auth: VaultAuth,
        *,
        namespace: str | None = None,
        verify: bool | str = True,
        timeout: float = 30.0,
    ) -> None:
        """Construct a VaultSource.

        Args:
            addr: Base URL of the Vault server (e.g.
                ``"https://vault.example.com"``).
            auth: Auth descriptor — `TokenAuth`, `AppRoleAuth`, or
                `KubernetesAuth`.
            namespace: Optional Vault Enterprise namespace
                (e.g. ``"dagstack/prod"``).
            verify: TLS verification — ``True`` (system CAs), a path to
                a CA bundle, or ``False`` (insecure, dev-only).
            timeout: Per-request timeout in seconds.
        """
        self._addr = addr
        self._auth = auth
        self._namespace = namespace
        self._id = f"vault:{addr}" + (f"#{namespace}" if namespace else "")

        client = hvac.Client(
            url=addr,
            namespace=namespace,
            verify=verify,
            timeout=timeout,
        )
        self._client = self._authenticate(client, auth)

    @property
    def id(self) -> str:
        return self._id

    @staticmethod
    def _authenticate(client: hvac.Client, auth: VaultAuth) -> hvac.Client:
        """Authenticate the hvac client according to the auth descriptor type."""
        if isinstance(auth, TokenAuth):
            client.token = auth.token
        elif isinstance(auth, AppRoleAuth):
            try:
                resp = client.auth.approle.login(
                    role_id=auth.role_id,
                    secret_id=auth.secret_id,
                    mount_point=auth.mount_point,
                )
            except Forbidden as exc:
                raise ConfigError(
                    path="",
                    reason=ConfigErrorReason.SECRET_PERMISSION_DENIED,
                    details=f"Vault AppRole login rejected: {exc}",
                    source_id=f"vault:{client.url}",
                ) from exc
            except VaultError as exc:
                raise ConfigError(
                    path="",
                    reason=ConfigErrorReason.SECRET_BACKEND_UNAVAILABLE,
                    details=f"Vault AppRole login failed: {exc}",
                    source_id=f"vault:{client.url}",
                ) from exc
            client.token = resp["auth"]["client_token"]
        elif isinstance(auth, KubernetesAuth):
            jwt = _read_kubernetes_jwt(auth.jwt_path)
            try:
                resp = client.auth.kubernetes.login(
                    role=auth.role,
                    jwt=jwt,
                    mount_point=auth.mount_point,
                )
            except Forbidden as exc:
                raise ConfigError(
                    path="",
                    reason=ConfigErrorReason.SECRET_PERMISSION_DENIED,
                    details=f"Vault Kubernetes login rejected: {exc}",
                    source_id=f"vault:{client.url}",
                ) from exc
            except VaultError as exc:
                raise ConfigError(
                    path="",
                    reason=ConfigErrorReason.SECRET_BACKEND_UNAVAILABLE,
                    details=f"Vault Kubernetes login failed: {exc}",
                    source_id=f"vault:{client.url}",
                ) from exc
            client.token = resp["auth"]["client_token"]

        if not client.is_authenticated():  # pragma: no cover — defence in depth
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_PERMISSION_DENIED,
                details=(
                    "Vault client.is_authenticated() returned False after "
                    "auth handshake — check the auth method and credentials"
                ),
                source_id=f"vault:{client.url}",
            )
        return client

    def resolve(self, path: str, ctx: ResolveContext) -> SecretValue:
        """Resolve a SecretRef.path against KV v2.

        Path layout per ADR-0002 §6.3:
            <mount-point>/<key-path>[?version=N][#field]

        The leading segment is the KV v2 mount point (default Vault
        setup uses ``secret``). The remainder, plus any optional
        ``?version=`` query and ``#field`` projection, is the
        backend-side address.
        """
        del ctx  # Phase 2 does not honour cancellation/deadline;
        # the async path lands with AsyncSecretSource.

        mount_point, key_path, version, field_name = _parse_vault_path(path)

        try:
            response = self._client.secrets.kv.v2.read_secret_version(
                path=key_path,
                version=version,
                mount_point=mount_point,
                raise_on_deleted_version=True,
            )
        except InvalidPath as exc:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_UNRESOLVED,
                details=f"Vault read of {mount_point}/{key_path} failed: not found (InvalidPath: {exc})",
                source_id=self._id,
            ) from exc
        except Forbidden as exc:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_PERMISSION_DENIED,
                details=(
                    f"Vault read of {mount_point}/{key_path} failed: rejected (Forbidden: {exc}); "
                    f"check the Vault policy attached to this token / role"
                ),
                source_id=self._id,
            ) from exc
        except InvalidRequest as exc:
            # Includes "Version N not found" / "Version N destroyed".
            version_part = f" version={version}" if version else ""
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_UNRESOLVED,
                details=(
                    f"Vault read of {mount_point}/{key_path}{version_part} failed: "
                    f"rejected (InvalidRequest: {exc})"
                ),
                source_id=self._id,
            ) from exc
        except VaultDown as exc:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_BACKEND_UNAVAILABLE,
                details=f"Vault read of {mount_point}/{key_path} failed: backend sealed or down ({exc})",
                source_id=self._id,
            ) from exc
        except VaultError as exc:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_BACKEND_UNAVAILABLE,
                details=f"Vault read of {mount_point}/{key_path} failed: {exc}",
                source_id=self._id,
            ) from exc

        # KV v2 envelope: {data: {data: {...secret}, metadata: {...}}}
        try:
            secret_data: dict[str, Any] = response["data"]["data"]
            metadata: dict[str, Any] = response["data"]["metadata"]
        except (KeyError, TypeError) as exc:  # pragma: no cover — defence in depth
            # NB: do NOT include the response payload — it contains the
            # secret data on success paths, and a malformed-but-still-
            # populated envelope would leak it into logs/diagnostics.
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_BACKEND_UNAVAILABLE,
                details=(
                    f"Vault response for {mount_point}/{key_path} has unexpected "
                    f"envelope shape (missing 'data.data' / 'data.metadata' keys)"
                ),
                source_id=self._id,
            ) from exc

        # ADR-0002 §1.2 normative behaviour:
        # - if `#field` is specified, pluck `secret_data[field]`;
        # - if no `#field` but the value is multi-key, raise the
        #   verbatim normative message;
        # - if no `#field` and the value is single-key, unwrap the only
        #   value (operator convenience — common Vault layout for
        #   single-secret entries).
        if not secret_data:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_UNRESOLVED,
                details=f"Vault {mount_point}/{key_path} contains an empty secret",
                source_id=self._id,
            )

        if field_name is not None:
            if field_name not in secret_data:
                raise ConfigError(
                    path="",
                    reason=ConfigErrorReason.SECRET_UNRESOLVED,
                    details=(
                        f"Vault {mount_point}/{key_path} has no field "
                        f"{field_name!r} (available keys: {sorted(secret_data)})"
                    ),
                    source_id=self._id,
                )
            value: Any = secret_data[field_name]
        elif len(secret_data) > 1:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_UNRESOLVED,
                details=(
                    "reference resolved to object; specify a sub-key with '#field' "
                    f"(available keys: {sorted(secret_data)})"
                ),
                source_id=self._id,
            )
        else:
            # Single-key envelope — unwrap the only value.
            (value,) = secret_data.values()

        if not isinstance(value, str):
            # KV v2 stores everything as JSON; non-string scalars are
            # numbers / bools / lists / dicts. Phase 2 SecretValue is
            # always a string (ADR-0002 §3); coerce primitives via
            # `str()` so the loader can apply downstream getters.
            value = str(value)

        version_str = (
            str(metadata["version"])
            if isinstance(metadata, dict) and "version" in metadata
            else None
        )
        expires_at: datetime | None = None
        if isinstance(metadata, dict):
            # KV v2 sets custom_metadata to None (not missing) when the
            # operator did not attach any. Coerce to {} explicitly.
            custom = metadata.get("custom_metadata") or {}
            ttl = custom.get("ttl_seconds")
            if isinstance(ttl, (int, str)):
                try:
                    # Use UTC for cross-process reproducibility; the
                    # consumer can convert to local time when displaying.
                    expires_at = datetime.now(tz=UTC) + timedelta(seconds=int(ttl))
                except ValueError:
                    expires_at = None

        return SecretValue(
            value=value,
            source_id=self._id,
            version=version_str,
            expires_at=expires_at,
        )

    def close(self) -> None:
        """Release any resources held by the underlying hvac client.

        hvac uses `requests.Session` under the hood — `close()` releases
        the connection pool. Token revocation is NOT performed
        automatically; operators that want it must call
        `client.auth.token.revoke_self()` themselves before close.
        """
        adapter = getattr(self._client, "_adapter", None)
        if adapter is not None:
            session = getattr(adapter, "session", None)
            if session is not None and hasattr(session, "close"):
                session.close()


# ── Helpers ────────────────────────────────────────────────────────────


def _parse_vault_path(
    path: str,
) -> tuple[str, str, int | None, str | None]:
    """Split ``<mount>/<key>[?version=N][#field]`` into components.

    Returns (mount_point, key_path, version_or_None, field_name_or_None).

    Raises:
        ConfigError(SECRET_UNRESOLVED): malformed path (no mount-point
            segment, malformed query value, etc.).
    """
    # Strip the optional `#field` tail.
    field_name: str | None = None
    if "#" in path:
        path, field_name = path.split("#", 1)

    # Split off the optional `?query`.
    if "?" in path:
        path, query = path.split("?", 1)
    else:
        query = ""

    # The first segment is the mount point; the rest is the key path.
    if "/" not in path:
        raise ConfigError(
            path="",
            reason=ConfigErrorReason.SECRET_UNRESOLVED,
            details=(
                f"Vault path {path!r} does not include a mount-point segment "
                f"(expected '<mount>/<key-path>', e.g. 'secret/dagstack/db')"
            ),
        )
    mount_point, key_path = path.split("/", 1)

    version: int | None = None
    if query:
        params = parse_qs(query, keep_blank_values=True)
        version_values: list[str] = params.get("version", [])
        if version_values:
            try:
                version = int(version_values[0])
            except ValueError as exc:
                raise ConfigError(
                    path="",
                    reason=ConfigErrorReason.SECRET_UNRESOLVED,
                    details=(
                        f"Vault path {path!r} has invalid ?version= value "
                        f"{version_values[0]!r}: must be an integer"
                    ),
                ) from exc
        # Reject unknown query keys per ADR-0002 §4 rule (bindings MUST
        # reject unknown query keys with secret_unresolved).
        unknown: Iterable[str] = (k for k in params if k != "version")
        unknown_keys = list(unknown)
        if unknown_keys:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_UNRESOLVED,
                details=(
                    f"Vault path {path!r} has unknown query parameter(s) "
                    f"{unknown_keys!r}; only 'version' is recognised in Phase 2"
                ),
            )

    return mount_point, key_path, version, field_name


def _read_kubernetes_jwt(jwt_path: str) -> str:
    """Read the projected ServiceAccount JWT from the pod filesystem."""
    try:
        with open(jwt_path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError as exc:
        raise ConfigError(
            path="",
            reason=ConfigErrorReason.SECRET_BACKEND_UNAVAILABLE,
            details=(
                f"cannot read Kubernetes ServiceAccount token at {jwt_path!r}: {exc} "
                f"(running outside a pod? misconfigured projected token?)"
            ),
        ) from exc
