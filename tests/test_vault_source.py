"""Unit tests for `dagstack.config.vault.VaultSource`.

Covers the path-parsing grammar, KV v2 envelope handling, ``#field``
projection, ``?version=`` query, and auth method dispatch via mocks.
testcontainers-based integration tests against a real Vault dev server
land in a follow-up PR alongside the conformance/vault/ fixtures from
config-spec issue #18 slice 2.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dagstack.config import Config, ConfigError, ConfigErrorReason, InMemorySource
from dagstack.config.secrets import ResolveContext

# Skip the whole module if hvac isn't installed (consumer didn't pull the
# `[vault]` extra). Lets the suite stay green for the non-Vault install.
hvac = pytest.importorskip("hvac")

from dagstack.config.vault import (  # noqa: E402
    AppRoleAuth,
    KubernetesAuth,
    TokenAuth,
    VaultSource,
    _parse_vault_path,
)

# ── Path parser ────────────────────────────────────────────────────────


class TestParseVaultPath:
    def test_minimal(self) -> None:
        assert _parse_vault_path("secret/db") == ("secret", "db", None, None)

    def test_with_subkey(self) -> None:
        assert _parse_vault_path("secret/db#password") == (
            "secret",
            "db",
            None,
            "password",
        )

    def test_with_version(self) -> None:
        assert _parse_vault_path("secret/db?version=3") == (
            "secret",
            "db",
            3,
            None,
        )

    def test_with_version_and_subkey(self) -> None:
        assert _parse_vault_path("secret/dagstack/prod/db?version=5#password") == (
            "secret",
            "dagstack/prod/db",
            5,
            "password",
        )

    def test_no_mount_point_segment_raises(self) -> None:
        with pytest.raises(ConfigError) as exc:
            _parse_vault_path("just-a-path")
        assert exc.value.reason == ConfigErrorReason.SECRET_UNRESOLVED
        assert "mount-point segment" in (exc.value.details or "")

    def test_invalid_version_raises(self) -> None:
        with pytest.raises(ConfigError) as exc:
            _parse_vault_path("secret/db?version=latest")
        assert exc.value.reason == ConfigErrorReason.SECRET_UNRESOLVED
        assert "must be an integer" in (exc.value.details or "")

    def test_unknown_query_key_raises(self) -> None:
        with pytest.raises(ConfigError) as exc:
            _parse_vault_path("secret/db?colour=red")
        assert exc.value.reason == ConfigErrorReason.SECRET_UNRESOLVED
        assert "unknown query parameter" in (exc.value.details or "")


# ── Auth dispatch ─────────────────────────────────────────────────────


def _stub_authenticated_client() -> MagicMock:
    """Build a hvac.Client mock that reports authenticated."""
    client = MagicMock()
    client.is_authenticated.return_value = True
    client.url = "https://vault.example.com"
    return client


class TestAuthMethods:
    def test_token_auth(self) -> None:
        with patch("dagstack.config.vault.hvac.Client") as cls:
            cls.return_value = _stub_authenticated_client()
            src = VaultSource(
                addr="https://vault.example.com",
                auth=TokenAuth(token="s.abc123"),
            )
            assert src._client.token == "s.abc123"

    def test_approle_auth(self) -> None:
        with patch("dagstack.config.vault.hvac.Client") as cls:
            client = _stub_authenticated_client()
            client.auth.approle.login.return_value = {"auth": {"client_token": "s.from-approle"}}
            cls.return_value = client
            src = VaultSource(
                addr="https://vault.example.com",
                auth=AppRoleAuth(role_id="role-x", secret_id="sec-y"),
            )
            client.auth.approle.login.assert_called_once_with(
                role_id="role-x",
                secret_id="sec-y",
                mount_point="approle",
            )
            assert src._client.token == "s.from-approle"

    def test_approle_auth_forbidden_raises_permission_denied(self) -> None:
        from hvac.exceptions import Forbidden

        with patch("dagstack.config.vault.hvac.Client") as cls:
            client = _stub_authenticated_client()
            client.auth.approle.login.side_effect = Forbidden("bad creds")
            cls.return_value = client
            with pytest.raises(ConfigError) as exc:
                VaultSource(
                    addr="https://vault.example.com",
                    auth=AppRoleAuth(role_id="bad", secret_id="bad"),
                )
            assert exc.value.reason == ConfigErrorReason.SECRET_PERMISSION_DENIED

    def test_kubernetes_auth_reads_jwt(self, tmp_path: Any) -> None:
        jwt_file = tmp_path / "token"
        jwt_file.write_text("eyJ.test.jwt")

        with patch("dagstack.config.vault.hvac.Client") as cls:
            client = _stub_authenticated_client()
            client.auth.kubernetes.login.return_value = {"auth": {"client_token": "s.from-k8s"}}
            cls.return_value = client
            src = VaultSource(
                addr="https://vault.example.com",
                auth=KubernetesAuth(role="my-role", jwt_path=str(jwt_file)),
            )
            client.auth.kubernetes.login.assert_called_once_with(
                role="my-role",
                jwt="eyJ.test.jwt",
                mount_point="kubernetes",
            )
            assert src._client.token == "s.from-k8s"

    def test_kubernetes_auth_missing_jwt_raises(self) -> None:
        with patch("dagstack.config.vault.hvac.Client") as cls:
            cls.return_value = _stub_authenticated_client()
            with pytest.raises(ConfigError) as exc:
                VaultSource(
                    addr="https://vault.example.com",
                    auth=KubernetesAuth(role="r", jwt_path="/nonexistent/jwt-file"),
                )
            assert exc.value.reason == ConfigErrorReason.SECRET_BACKEND_UNAVAILABLE
            assert "Kubernetes ServiceAccount token" in (exc.value.details or "")


# ── Resolve / KV v2 ───────────────────────────────────────────────────


def _make_source_with_secret(secret_data: dict[str, Any]) -> VaultSource:
    """Build a VaultSource whose hvac.Client returns `secret_data`."""
    with patch("dagstack.config.vault.hvac.Client") as cls:
        client = _stub_authenticated_client()
        client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {
                "data": secret_data,
                "metadata": {"version": 7},
            }
        }
        cls.return_value = client
        return VaultSource(
            addr="https://vault.example.com",
            auth=TokenAuth(token="s.test"),
        )


class TestResolveSingleKey:
    def test_unwraps_single_key_envelope(self) -> None:
        src = _make_source_with_secret({"value": "sk-xyz"})
        result = src.resolve("secret/openai", ResolveContext())
        assert result.value == "sk-xyz"
        assert result.version == "7"
        assert result.source_id == "vault:https://vault.example.com"

    def test_coerces_non_string_to_str(self) -> None:
        src = _make_source_with_secret({"port": 8080})
        result = src.resolve("secret/db", ResolveContext())
        assert result.value == "8080"


class TestResolveMultiKey:
    def test_multi_key_without_field_raises_normative(self) -> None:
        src = _make_source_with_secret({"username": "u", "password": "p"})
        with pytest.raises(ConfigError) as exc:
            src.resolve("secret/db", ResolveContext())
        assert exc.value.reason == ConfigErrorReason.SECRET_UNRESOLVED
        # Verbatim §1.2 normative message.
        assert "reference resolved to object; specify a sub-key with '#field'" in (
            exc.value.details or ""
        )
        assert "['password', 'username']" in (exc.value.details or "")

    def test_field_projection(self) -> None:
        src = _make_source_with_secret({"username": "u", "password": "p"})
        result = src.resolve("secret/db#password", ResolveContext())
        assert result.value == "p"

    def test_field_projection_unknown_field(self) -> None:
        src = _make_source_with_secret({"username": "u", "password": "p"})
        with pytest.raises(ConfigError) as exc:
            src.resolve("secret/db#missing", ResolveContext())
        assert exc.value.reason == ConfigErrorReason.SECRET_UNRESOLVED
        assert "no field 'missing'" in (exc.value.details or "")
        assert "['password', 'username']" in (exc.value.details or "")


class TestResolveErrors:
    def test_invalid_path_raises_secret_unresolved(self) -> None:
        from hvac.exceptions import InvalidPath

        with patch("dagstack.config.vault.hvac.Client") as cls:
            client = _stub_authenticated_client()
            client.secrets.kv.v2.read_secret_version.side_effect = InvalidPath("not found")
            cls.return_value = client
            src = VaultSource(addr="https://vault.example.com", auth=TokenAuth(token="s.x"))
        with pytest.raises(ConfigError) as exc:
            src.resolve("secret/missing", ResolveContext())
        assert exc.value.reason == ConfigErrorReason.SECRET_UNRESOLVED

    def test_forbidden_raises_permission_denied(self) -> None:
        from hvac.exceptions import Forbidden

        with patch("dagstack.config.vault.hvac.Client") as cls:
            client = _stub_authenticated_client()
            client.secrets.kv.v2.read_secret_version.side_effect = Forbidden("403")
            cls.return_value = client
            src = VaultSource(addr="https://vault.example.com", auth=TokenAuth(token="s.x"))
        with pytest.raises(ConfigError) as exc:
            src.resolve("secret/protected", ResolveContext())
        assert exc.value.reason == ConfigErrorReason.SECRET_PERMISSION_DENIED

    def test_vault_down_raises_backend_unavailable(self) -> None:
        from hvac.exceptions import VaultDown

        with patch("dagstack.config.vault.hvac.Client") as cls:
            client = _stub_authenticated_client()
            client.secrets.kv.v2.read_secret_version.side_effect = VaultDown("sealed")
            cls.return_value = client
            src = VaultSource(addr="https://vault.example.com", auth=TokenAuth(token="s.x"))
        with pytest.raises(ConfigError) as exc:
            src.resolve("secret/x", ResolveContext())
        assert exc.value.reason == ConfigErrorReason.SECRET_BACKEND_UNAVAILABLE


class TestResolveVersion:
    def test_version_passed_to_hvac(self) -> None:
        src = _make_source_with_secret({"value": "v3-content"})
        src.resolve("secret/db?version=3", ResolveContext())
        src._client.secrets.kv.v2.read_secret_version.assert_called_with(
            path="db",
            version=3,
            mount_point="secret",
            raise_on_deleted_version=True,
        )


# ── End-to-end via Config ─────────────────────────────────────────────


class TestEndToEnd:
    def test_loader_picks_vault_scheme(self) -> None:
        with patch("dagstack.config.vault.hvac.Client") as cls:
            client = _stub_authenticated_client()
            client.secrets.kv.v2.read_secret_version.return_value = {
                "data": {
                    "data": {"api_key": "sk-from-vault"},
                    "metadata": {"version": 1},
                }
            }
            cls.return_value = client
            vault = VaultSource(addr="https://vault.example.com", auth=TokenAuth(token="s.x"))

        src = InMemorySource({"llm": {"api_key": "${secret:vault:secret/openai#api_key}"}})
        cfg = Config.load_from([src, vault])
        assert cfg.get_string("llm.api_key") == "sk-from-vault"

    def test_namespace_is_part_of_id(self) -> None:
        with patch("dagstack.config.vault.hvac.Client") as cls:
            cls.return_value = _stub_authenticated_client()
            src = VaultSource(
                addr="https://vault.example.com",
                auth=TokenAuth(token="s.x"),
                namespace="dagstack/prod",
            )
        assert src.id == "vault:https://vault.example.com#dagstack/prod"
