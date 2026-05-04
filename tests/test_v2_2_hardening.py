"""Unit tests for v2.2 hardening: array-path + secrets + walker invariant.

Cover conformance fixtures tagged `runner_extension_required` that the
v1.0 runner does not model (getter/getSection-level).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from dagstack.config import (
    Config,
    ConfigError,
    ConfigErrorReason,
    InMemorySource,
)
from dagstack.config.secrets_mask import (
    MASKED_PLACEHOLDER,
    is_secret_field,
    mask_value,
)

# ── §4.2 / §4.5 Array indices in ConfigError.path ──────────────────


class _Server(BaseModel):
    host: str
    port: int


class _DbWithServers(BaseModel):
    servers: list[_Server]


class TestArrayPathPreservation:
    def test_nested_array_index_uses_bracket_form(self) -> None:
        cfg = Config.load_from(
            [
                InMemorySource(
                    {
                        "database": {
                            "servers": [
                                {"host": "localhost", "port": 5432},
                                {"host": "replica.example.com", "port": "not-a-number"},
                            ],
                        },
                    }
                ),
            ]
        )
        with pytest.raises(ConfigError) as exc:
            cfg.get_section("database", _DbWithServers)

        # ADR v2.2 §4.2: array index formatted as [N], not .N.
        assert exc.value.path == "database.servers[1].port"
        assert exc.value.reason is ConfigErrorReason.VALIDATION_FAILED


# ── §6 Secrets masking ──────────────────────────────────────────────


class TestSecretPatterns:
    @pytest.mark.parametrize(
        "name",
        ["api_key", "db_password", "auth_token", "access_key", "private_key", "APIKEY"],
    )
    def test_matches_secret_patterns(self, name: str) -> None:
        assert is_secret_field(name)

    @pytest.mark.parametrize("name", ["host", "port", "name", "pool_size", "url"])
    def test_non_secret_fields(self, name: str) -> None:
        assert not is_secret_field(name)

    def test_mask_value_replaces_nonempty(self) -> None:
        assert mask_value("api_key", "sk-abc123") == MASKED_PLACEHOLDER

    def test_mask_value_preserves_empty(self) -> None:
        # Empty values are not masked — there is nothing to hide.
        assert mask_value("api_key", "") == ""
        assert mask_value("api_key", None) is None

    def test_mask_value_non_secret_passthrough(self) -> None:
        assert mask_value("host", "prod.example.com") == "prod.example.com"


class _SecretConfig(BaseModel):
    host: str
    api_key: str = Field(..., min_length=10)  # an invalid short value will fail


class TestSecretMaskingInErrorDetails:
    def test_short_secret_masked_in_details(self) -> None:
        cfg = Config.load_from(
            [
                InMemorySource({"service": {"host": "localhost", "api_key": "sk-short"}}),
            ]
        )
        with pytest.raises(ConfigError) as exc:
            cfg.get_section("service", _SecretConfig)

        # The secret value must not appear in details.
        assert "sk-short" not in exc.value.details
        # MASKED placeholder is present.
        assert MASKED_PLACEHOLDER in exc.value.details
        # The full path is preserved.
        assert exc.value.path == "service.api_key"


# ── §4.4 Walker invariant ───────────────────────────────────────────


class _DbInt(BaseModel):
    port: int


class TestWalkerInvariant:
    def test_get_returns_raw_env_string(self) -> None:
        # get() raw — env-substituted string stays a string.
        cfg = Config.load_from([InMemorySource({"database": {"port": "5432"}})])
        raw = cfg.get("database.port")
        assert raw == "5432"  # a string, not int
        assert isinstance(raw, str)

    def test_get_int_coerces_via_regex(self) -> None:
        # get_int — coerce per §4.3 primitive rule.
        cfg = Config.load_from([InMemorySource({"database": {"port": "5432"}})])
        assert cfg.get_int("database.port") == 5432

    def test_get_section_coerces_via_schema_walker(self) -> None:
        # get_section — coerce walker + schema validation.
        cfg = Config.load_from([InMemorySource({"database": {"port": "5432"}})])
        db = cfg.get_section("database", _DbInt)
        assert db.port == 5432
        assert isinstance(db.port, int)
