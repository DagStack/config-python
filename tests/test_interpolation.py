"""Unit tests for env interpolation."""

from __future__ import annotations

import pytest

from dagstack.config.errors import ConfigError, ConfigErrorReason
from dagstack.config.interpolation import has_interpolation, interpolate


class TestBasicInterpolation:
    def test_plain_variable(self) -> None:
        result = interpolate("${HOST}", env={"HOST": "localhost"})
        assert result == "localhost"

    def test_variable_embedded_in_string(self) -> None:
        result = interpolate("http://${HOST}:8080", env={"HOST": "example.com"})
        assert result == "http://example.com:8080"

    def test_multiple_variables(self) -> None:
        result = interpolate(
            "${PROTO}://${HOST}:${PORT}",
            env={"PROTO": "https", "HOST": "api.example.com", "PORT": "443"},
        )
        assert result == "https://api.example.com:443"

    def test_no_interpolation_plain_text(self) -> None:
        result = interpolate("just a string without placeholders", env={})
        assert result == "just a string without placeholders"

    def test_empty_string(self) -> None:
        assert interpolate("", env={}) == ""


class TestDefaultValues:
    def test_default_used_when_var_missing(self) -> None:
        result = interpolate("${HOST:-localhost}", env={})
        assert result == "localhost"

    def test_default_used_when_var_empty(self) -> None:
        # An empty string triggers the default (per spec §2: "not set or empty").
        result = interpolate("${HOST:-localhost}", env={"HOST": ""})
        assert result == "localhost"

    def test_env_wins_over_default(self) -> None:
        result = interpolate("${HOST:-localhost}", env={"HOST": "production.example.com"})
        assert result == "production.example.com"

    def test_default_can_contain_spaces(self) -> None:
        result = interpolate("${MSG:-hello world}", env={})
        assert result == "hello world"

    def test_default_can_contain_url(self) -> None:
        result = interpolate(
            "${URL:-http://localhost:11434/v1}",
            env={},
        )
        assert result == "http://localhost:11434/v1"

    def test_empty_default(self) -> None:
        result = interpolate("${EMPTY:-}", env={})
        assert result == ""

    def test_nested_interpolation_in_default_not_resolved(self) -> None:
        # Per spec §2: "nested ${...} in defaults is not interpolated".
        # The default is a literal string.
        result = interpolate("${FOO:-${BAR}}", env={"BAR": "ignored"})
        # The regex matches up to the first `}`, so `${FOO:-${BAR}` is
        # parsed as expr="FOO:-${BAR" without a closing brace for the
        # nested form; default = "${BAR". The trailing `}` is left as
        # the remainder of the string.
        assert result == "${BAR}"


class TestUnresolvedVariables:
    def test_missing_var_without_default_raises(self) -> None:
        with pytest.raises(ConfigError) as exc_info:
            interpolate("${MISSING_VAR}", env={})
        err = exc_info.value
        assert err.reason is ConfigErrorReason.ENV_UNRESOLVED
        assert "MISSING_VAR" in err.details

    def test_error_includes_path_when_provided(self) -> None:
        with pytest.raises(ConfigError) as exc_info:
            interpolate("${MISSING}", env={}, path="llm.api_key")
        assert exc_info.value.path == "llm.api_key"

    def test_error_includes_source_id_when_provided(self) -> None:
        with pytest.raises(ConfigError) as exc_info:
            interpolate("${MISSING}", env={}, source_id="yaml:app-config.yaml")
        assert exc_info.value.source_id == "yaml:app-config.yaml"


class TestEscaping:
    def test_double_dollar_is_literal_dollar(self) -> None:
        result = interpolate("cost: $$100", env={})
        assert result == "cost: $100"

    def test_double_dollar_does_not_block_interpolation_when_separated(self) -> None:
        # `$$` is an escape, `${VAR}` is interpolation; separated by a
        # space they do not interfere.
        # NB: `$${VAR}` without a separator — the escape consumes `$$`,
        # the remainder `{VAR}` is literal (Compose semantics, see
        # test_dollar_pair_followed_by_brace_literal).
        result = interpolate("$$ ${HOST}", env={"HOST": "x"})
        assert result == "$ x"

    def test_dollar_pair_followed_by_brace_literal(self) -> None:
        # `$${A}` — escape → `$`, remainder `{A}` is literal (not interpolation).
        result = interpolate("$${A}", env={"A": "v"})
        assert result == "${A}"

    def test_multiple_dollar_signs(self) -> None:
        # 5 dollars = 2 escaped pairs + `${A}` = literal `$$` + interpolated `v`.
        result = interpolate("$$$$${A}", env={"A": "v"})
        assert result == "$$v"


class TestEdgeCases:
    def test_whitespace_around_var_name_stripped(self) -> None:
        result = interpolate("${ HOST }", env={"HOST": "x"})
        assert result == "x"

    def test_var_name_with_underscores_and_digits(self) -> None:
        result = interpolate(
            "${HTTP_PORT_1}",
            env={"HTTP_PORT_1": "8080"},
        )
        assert result == "8080"

    def test_single_dollar_without_braces_untouched(self) -> None:
        # `$HOST` (without braces) is not our syntax and is left as-is.
        result = interpolate("$HOST", env={"HOST": "x"})
        assert result == "$HOST"

    def test_unclosed_brace_treated_as_literal(self) -> None:
        # `${VAR` (without a closing brace) — the parser soft-fails, does
        # not raise; `${` remains literal.
        result = interpolate("prefix ${VAR_no_close", env={})
        assert result == "prefix ${VAR_no_close"

    def test_dollar_at_end_of_string(self) -> None:
        # A trailing `$` without a following character is literal.
        result = interpolate("value$", env={})
        assert result == "value$"


class TestHasInterpolation:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("no placeholders here", False),
            ("${VAR}", True),
            ("prefix ${VAR} suffix", True),
            ("${VAR:-default}", True),
            ("$$not interpolation", False),
            ("", False),
        ],
    )
    def test_detection(self, text: str, expected: bool) -> None:
        assert has_interpolation(text) is expected
