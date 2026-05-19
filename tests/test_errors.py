"""Unit tests for ConfigError + ConfigErrorReason."""

from __future__ import annotations

import pytest

from dagstack.config.errors import ConfigError, ConfigErrorReason


class TestConfigErrorReason:
    def test_enum_values_stable(self) -> None:
        # These string values are part of the contract (the emitter will
        # emit them); do not change accidentally. Renaming requires
        # coordination with the spec and other bindings.
        assert ConfigErrorReason.MISSING.value == "missing"
        assert ConfigErrorReason.TYPE_MISMATCH.value == "type_mismatch"
        assert ConfigErrorReason.ENV_UNRESOLVED.value == "env_unresolved"
        assert ConfigErrorReason.VALIDATION_FAILED.value == "validation_failed"
        assert ConfigErrorReason.PARSE_ERROR.value == "parse_error"
        assert ConfigErrorReason.SOURCE_UNAVAILABLE.value == "source_unavailable"
        assert ConfigErrorReason.RELOAD_REJECTED.value == "reload_rejected"

    def test_is_str_enum(self) -> None:
        # StrEnum simplifies serialization (prints as string value, not as an Enum instance).
        assert isinstance(ConfigErrorReason.MISSING, str)
        assert str(ConfigErrorReason.MISSING) == "missing"


class TestConfigError:
    def test_required_fields_accessible(self) -> None:
        err = ConfigError(
            path="llm.api_key",
            reason=ConfigErrorReason.MISSING,
            details="key absent in merged config",
        )
        assert err.path == "llm.api_key"
        assert err.reason is ConfigErrorReason.MISSING
        assert err.details == "key absent in merged config"
        assert err.source_id is None

    def test_source_id_optional(self) -> None:
        err = ConfigError(
            path="",
            reason=ConfigErrorReason.PARSE_ERROR,
            details="invalid yaml",
            source_id="yaml:config.yaml",
        )
        assert err.source_id == "yaml:config.yaml"

    def test_str_message_format(self) -> None:
        err = ConfigError(
            path="llm.base_url",
            reason=ConfigErrorReason.ENV_UNRESOLVED,
            details="env variable 'OPENAI_BASE_URL' is not set",
        )
        msg = str(err)
        assert "env_unresolved" in msg
        assert "llm.base_url" in msg
        assert "OPENAI_BASE_URL" in msg

    def test_str_message_includes_source(self) -> None:
        err = ConfigError(
            path="",
            reason=ConfigErrorReason.SOURCE_UNAVAILABLE,
            details="connection refused",
            source_id="etcd://prod/dagstack",
        )
        msg = str(err)
        assert "etcd://prod/dagstack" in msg

    def test_empty_path_formatted_without_prefix(self) -> None:
        err = ConfigError(
            path="",
            reason=ConfigErrorReason.PARSE_ERROR,
            details="bad yaml",
        )
        # No "at ''" prefix when path is empty — cleaner for top-level errors.
        assert "at ''" not in str(err)

    def test_repr_roundtrip_friendly(self) -> None:
        err = ConfigError(
            path="a.b",
            reason=ConfigErrorReason.TYPE_MISMATCH,
            details="expected int",
        )
        rep = repr(err)
        assert "ConfigError" in rep
        assert "a.b" in rep
        assert "type_mismatch" in rep

    def test_is_exception_subclass(self) -> None:
        # For try/except and logging compatibility.
        err = ConfigError(path="", reason=ConfigErrorReason.MISSING, details="x")
        assert isinstance(err, Exception)

    def test_raises_via_raise_statement(self) -> None:
        with pytest.raises(ConfigError) as exc_info:
            raise ConfigError(
                path="foo",
                reason=ConfigErrorReason.MISSING,
                details="bar",
            )
        assert exc_info.value.reason is ConfigErrorReason.MISSING

    def test_init_requires_keyword_args(self) -> None:
        # Positional args are forbidden — reduces the risk of mixing up the order.
        with pytest.raises(TypeError):
            ConfigError("path", ConfigErrorReason.MISSING, "details")  # type: ignore[misc]
