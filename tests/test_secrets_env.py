"""Unit tests for `EnvSecretSource` and related types."""

from __future__ import annotations

import pytest

from dagstack.config import (
    ConfigError,
    ConfigErrorReason,
    EnvSecretSource,
    ResolveContext,
    SecretRef,
    SecretSource,
    SecretValue,
)


class TestEnvSecretSource:
    def test_resolves_existing_var(self) -> None:
        src = EnvSecretSource(getenv={"OPENAI_API_KEY": "sk-xyz"}.get)
        result = src.resolve("OPENAI_API_KEY", ResolveContext())
        assert isinstance(result, SecretValue)
        assert result.value == "sk-xyz"
        assert result.source_id == "env:os.environ"

    def test_missing_var_raises_secret_unresolved(self) -> None:
        src = EnvSecretSource(getenv=lambda _name: None)
        with pytest.raises(ConfigError) as exc:
            src.resolve("MISSING_VAR", ResolveContext())
        assert exc.value.reason == ConfigErrorReason.SECRET_UNRESOLVED
        assert "MISSING_VAR" in (exc.value.details or "")

    def test_scheme_is_env(self) -> None:
        assert EnvSecretSource.scheme == "env"

    def test_id_stable(self) -> None:
        src = EnvSecretSource()
        assert src.id == "env:os.environ"

    def test_close_is_noop(self) -> None:
        # Just verify it doesn't raise.
        EnvSecretSource().close()

    def test_implements_secret_source_protocol(self) -> None:
        # Runtime-checkable protocol means isinstance works.
        assert isinstance(EnvSecretSource(), SecretSource)


class TestSecretRefEquality:
    def test_same_scheme_path_default_equal(self) -> None:
        a = SecretRef(scheme="env", path="K", default=None, origin_source="s1")
        b = SecretRef(scheme="env", path="K", default=None, origin_source="s2")
        # origin_source is compare=False — equality ignores it.
        assert a == b

    def test_different_default_not_equal(self) -> None:
        a = SecretRef(scheme="env", path="K", default="x")
        b = SecretRef(scheme="env", path="K", default="y")
        assert a != b
