"""Unit tests for `dagstack.config._secret_grammar`.

Covers the ${secret:<scheme>:<path>[?query][#field][:-default]} parser
per ADR-0002 v1.1 §1 grammar including the `??`/`##`/`::-` escape
rules and RFC 3986 percent-encoding for query values.
"""

from __future__ import annotations

import pytest

from dagstack.config import ConfigError, ConfigErrorReason, SecretRef
from dagstack.config._secret_grammar import (
    SECRET_REF_OUTER,
    parse_secret_ref,
    walk_secret_refs,
)


class TestSecretRefOuterRegex:
    def test_matches_minimal_token(self) -> None:
        assert SECRET_REF_OUTER.fullmatch("${secret:env:K}") is not None

    def test_extracts_inner_via_group(self) -> None:
        m = SECRET_REF_OUTER.fullmatch("${secret:vault:secret/dagstack/db#password}")
        assert m is not None
        assert m.group(1) == "vault:secret/dagstack/db#password"

    def test_does_not_match_phase1_var(self) -> None:
        # Phase 1 ${VAR} must not be confused with secret syntax.
        assert SECRET_REF_OUTER.fullmatch("${OPENAI_API_KEY}") is None


class TestParseSecretRefBasic:
    def test_env_scheme_minimal(self) -> None:
        ref = parse_secret_ref("env:OPENAI_API_KEY")
        assert ref == SecretRef(scheme="env", path="OPENAI_API_KEY", default=None)

    def test_with_default(self) -> None:
        ref = parse_secret_ref("env:VAR:-fallback-value")
        assert ref.scheme == "env"
        assert ref.path == "VAR"
        assert ref.default == "fallback-value"

    def test_with_field_projection(self) -> None:
        ref = parse_secret_ref("vault:secret/db#password")
        assert ref.scheme == "vault"
        assert ref.path == "secret/db#password"
        assert ref.default is None

    def test_with_query_and_field(self) -> None:
        ref = parse_secret_ref("vault:secret/db?version=3#password")
        assert ref.scheme == "vault"
        assert ref.path == "secret/db?version=3#password"

    def test_with_query_field_and_default(self) -> None:
        ref = parse_secret_ref("vault:secret/db?version=3#password:-fb")
        assert ref.path == "secret/db?version=3#password"
        assert ref.default == "fb"


class TestParseSecretRefScheme:
    def test_lowercase_alphanumeric(self) -> None:
        # `vault-dr` is not allowed (no hyphen in scheme grammar).
        # Only [a-z][a-z0-9_]*.
        ref = parse_secret_ref("vault_dr:path")
        assert ref.scheme == "vault_dr"

    def test_uppercase_scheme_rejected(self) -> None:
        with pytest.raises(ConfigError) as exc:
            parse_secret_ref("Vault:path")
        assert exc.value.reason == ConfigErrorReason.PARSE_ERROR

    def test_scheme_with_hyphen_rejected(self) -> None:
        with pytest.raises(ConfigError) as exc:
            parse_secret_ref("vault-dr:path")
        assert exc.value.reason == ConfigErrorReason.PARSE_ERROR

    def test_missing_separator(self) -> None:
        with pytest.raises(ConfigError) as exc:
            parse_secret_ref("envOPENAI_API_KEY")
        assert exc.value.reason == ConfigErrorReason.PARSE_ERROR
        assert "':' between scheme and path" in (exc.value.details or "")


class TestParseSecretRefEscapes:
    def test_doubled_hash_in_path(self) -> None:
        ref = parse_secret_ref("vault:tag##v2/db")
        assert ref.path == "tag#v2/db"

    def test_doubled_question_in_path(self) -> None:
        ref = parse_secret_ref("vault:where??name=foo")
        # `??` becomes literal `?` in path; not a query separator.
        assert ref.path == "where?name=foo"

    def test_doubled_colon_dash_in_path(self) -> None:
        ref = parse_secret_ref("vault:foo::-bar:-default")
        # `::-` is a literal `:-` inside path; only the unescaped
        # `:-` triggers the default-separator.
        assert ref.path == "foo:-bar"
        assert ref.default == "default"

    def test_unescaped_question_rejected(self) -> None:
        # Bare `?` in path without `??` escape would normally be the
        # query separator; an isolated `?` followed by no `=` produces
        # a malformed query, which the query parser catches.
        with pytest.raises(ConfigError) as exc:
            parse_secret_ref("vault:foo?bar")
        assert exc.value.reason == ConfigErrorReason.PARSE_ERROR


class TestParseSecretRefQuery:
    def test_single_query_param(self) -> None:
        ref = parse_secret_ref("vault:secret/db?version=3")
        assert ref.path == "secret/db?version=3"

    def test_percent_encoded_value(self) -> None:
        ref = parse_secret_ref("vault:secret/db?token=val%26with%3Dchars")
        assert ref.path == "secret/db?token=val&with=chars"

    def test_malformed_query_no_equals(self) -> None:
        with pytest.raises(ConfigError) as exc:
            parse_secret_ref("vault:db?orphan")
        assert exc.value.reason == ConfigErrorReason.PARSE_ERROR
        assert "missing '='" in (exc.value.details or "")


class TestWalkSecretRefs:
    def test_string_with_token_becomes_secret_ref(self) -> None:
        out = walk_secret_refs({"k": "${secret:env:VAR}"}, source_id="test")
        assert isinstance(out["k"], SecretRef)
        assert out["k"].scheme == "env"
        assert out["k"].path == "VAR"
        assert out["k"].origin_source == "test"

    def test_plain_string_passthrough(self) -> None:
        out = walk_secret_refs({"k": "literal"}, source_id="t")
        assert out["k"] == "literal"

    def test_nested_dict_recurses(self) -> None:
        out = walk_secret_refs({"db": {"pw": "${secret:env:PW}"}}, source_id="t")
        assert isinstance(out["db"]["pw"], SecretRef)

    def test_list_recurses(self) -> None:
        out = walk_secret_refs({"ks": ["${secret:env:A}", "${secret:env:B}"]}, source_id="t")
        assert all(isinstance(x, SecretRef) for x in out["ks"])

    def test_token_mixed_with_text_rejected(self) -> None:
        with pytest.raises(ConfigError) as exc:
            walk_secret_refs({"k": "prefix ${secret:env:V} suffix"}, source_id="t")
        assert exc.value.reason == ConfigErrorReason.PARSE_ERROR
        assert "must occupy the whole scalar value" in (exc.value.details or "")

    def test_non_string_leaves_passthrough(self) -> None:
        out = walk_secret_refs({"port": 8080, "ssl": True, "rate": 1.5}, source_id="t")
        assert out == {"port": 8080, "ssl": True, "rate": 1.5}
