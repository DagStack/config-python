"""ConfigError + ConfigErrorReason enum.

Per spec ADR-0001 §4.5: the structural fields of an error are fixed by
contract; per-language realization is idiomatic. The Python idiom is an
exception type.

Required fields: `path` (dot-notation), `reason` (enum), `details` (str).
Optional: `source_id` (which ConfigSource raised the error, if applicable).
"""

from __future__ import annotations

from enum import StrEnum


class ConfigErrorReason(StrEnum):
    """Fixed enum values per spec ADR-0001 §4.5 / `_meta/error_reasons.yaml`.

    Source of truth — the spec repo (`spec/_meta/error_reasons.yaml`,
    planned for emission into `_generated/` in Phase D). Currently
    hand-coded; the emitter will add a CI gate.
    """

    MISSING = "missing"
    """Key is absent and no default was provided."""

    TYPE_MISMATCH = "type_mismatch"
    """The value cannot be coerced to the requested type."""

    ENV_UNRESOLVED = "env_unresolved"
    """${ENV_VAR} without a default and the env var is not set."""

    VALIDATION_FAILED = "validation_failed"
    """Validation failed: schema validation in get_section, or
    loader-bootstrap configuration (e.g. duplicate SecretSource scheme
    registration per ADR-0002 §4)."""

    PARSE_ERROR = "parse_error"
    """YAML/JSON parse error while loading a source."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    """ConfigSource is unavailable (file missing, etcd unreachable, etc.)."""

    RELOAD_REJECTED = "reload_rejected"
    """The reconfigure candidate was rejected during validation phase (spec §8.4)."""

    # ── ADR-0002 (Phase 2 — secret resolution errors) ──────────────────
    SECRET_UNRESOLVED = "secret_unresolved"
    """A ${secret:<scheme>:<path>} reference cannot be resolved."""

    SECRET_BACKEND_UNAVAILABLE = "secret_backend_unavailable"
    """Secret backend unreachable (network/auth/timeout)."""

    SECRET_PERMISSION_DENIED = "secret_permission_denied"
    """Secret backend rejected the read with an authorisation error."""


class ConfigError(Exception):
    """Structured config error with the spec-defined fields.

    Fields:
        path: Dot-notation path within the config document
            (e.g. "database.host"). For top-level parse errors this is
            an empty string.
        reason: ConfigErrorReason enum value.
        details: Human-readable message with context.
        source_id: Which ConfigSource raised the error (if applicable).

    Usage:
        >>> raise ConfigError(
        ...     path="database.password",
        ...     reason=ConfigErrorReason.MISSING,
        ...     details="Required key not found in merged config",
        ... )
    """

    __slots__ = ("details", "path", "reason", "source_id")

    path: str
    reason: ConfigErrorReason
    details: str
    source_id: str | None

    def __init__(
        self,
        *,
        path: str,
        reason: ConfigErrorReason,
        details: str,
        source_id: str | None = None,
    ) -> None:
        self.path = path
        self.reason = reason
        self.details = details
        self.source_id = source_id
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        parts = [f"{self.reason.value} at {self.path!r}"] if self.path else [self.reason.value]
        parts.append(self.details)
        if self.source_id is not None:
            parts.append(f"source={self.source_id!r}")
        return ": ".join(parts)

    def __repr__(self) -> str:
        return (
            f"ConfigError(path={self.path!r}, reason={self.reason.value!r}, "
            f"details={self.details!r}, source_id={self.source_id!r})"
        )
