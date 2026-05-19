"""Integration tests for Config + SecretSource end-to-end (ADR-0002 §3 / §4)."""

from __future__ import annotations

import pytest

from dagstack.config import (
    Config,
    ConfigError,
    ConfigErrorReason,
    EnvSecretSource,
    InMemorySource,
    ResolveContext,
    SecretValue,
)


class _CountingEnv:
    """Test SecretSource that records every resolve() call."""

    scheme = "env"

    def __init__(self, table: dict[str, str]) -> None:
        self._table = table
        self.id = "test:counting"
        self.calls: list[str] = []

    def resolve(self, path: str, ctx: ResolveContext) -> SecretValue:
        del ctx  # unused in the test fixture
        self.calls.append(path)
        if path not in self._table:
            raise ConfigError(
                path="",
                reason=ConfigErrorReason.SECRET_UNRESOLVED,
                details=f"missing {path}",
                source_id=self.id,
            )
        return SecretValue(value=self._table[path], source_id=self.id)

    def close(self) -> None:
        pass


class TestEndToEnd:
    def test_env_passthrough_via_load_from(self) -> None:
        src = InMemorySource({"k": "${secret:env:VAR}"})
        env = EnvSecretSource(getenv={"VAR": "value"}.get)
        cfg = Config.load_from([src, env])
        assert cfg.get_string("k") == "value"

    def test_default_used_when_var_missing(self) -> None:
        src = InMemorySource({"k": "${secret:env:NO_SUCH_VAR:-fallback}"})
        env = EnvSecretSource(getenv=lambda _name: None)
        cfg = Config.load_from([src, env])
        assert cfg.get_string("k") == "fallback"

    def test_no_default_no_var_raises(self) -> None:
        src = InMemorySource({"k": "${secret:env:NO_SUCH_VAR}"})
        env = EnvSecretSource(getenv=lambda _name: None)
        cfg = Config.load_from([src, env])
        with pytest.raises(ConfigError) as exc:
            cfg.get_string("k")
        assert exc.value.reason == ConfigErrorReason.SECRET_UNRESOLVED

    def test_unknown_scheme_raises_with_default_handled(self) -> None:
        # No source for `vault` scheme — but default is provided so
        # resolution falls back without raising.
        src = InMemorySource({"k": "${secret:vault:secret/db#pw:-fallback}"})
        cfg = Config.load_from([src])
        assert cfg.get_string("k") == "fallback"

    def test_unknown_scheme_no_default_raises_at_load_time(self) -> None:
        # ADR-0002 §4 rule 3: unknown scheme MUST be detected at load
        # time, not at first read. The check is the eager scan inside
        # Config.load_from.
        src = InMemorySource({"k": "${secret:vault:secret/db#pw}"})
        with pytest.raises(ConfigError) as exc:
            Config.load_from([src])
        assert exc.value.reason == ConfigErrorReason.SECRET_UNRESOLVED
        assert "no SecretSource registered for scheme 'vault'" in (exc.value.details or "")
        # The error message lists the available schemes so the operator
        # can spot a typo.
        assert "available schemes:" in (exc.value.details or "")

    def test_unknown_scheme_with_default_loads_then_resolves_to_default(self) -> None:
        # The eager scan tolerates unknown schemes when the reference
        # has a fallback default — operator opts in to the
        # "preview-mode" of an upcoming backend.
        src = InMemorySource({"k": "${secret:vault:secret/db#pw:-fb}"})
        cfg = Config.load_from([src])
        assert cfg.get_string("k") == "fb"

    def test_field_projection_on_env_scheme_raises(self) -> None:
        # EnvSecretSource rejects ?query and #field in path: env values
        # are opaque single-value strings. The error is SECRET_UNRESOLVED
        # with a hint that structured secrets need a JSON-typed backend.
        src = InMemorySource({"k": "${secret:env:VAR#sub}"})
        env = EnvSecretSource(getenv={"VAR": "raw-value"}.get)
        cfg = Config.load_from([src, env])
        with pytest.raises(ConfigError) as exc:
            cfg.get_string("k")
        assert exc.value.reason == ConfigErrorReason.SECRET_UNRESOLVED
        assert "env scheme does not support sub-key projection" in (exc.value.details or "")
        assert "VaultSource" in (exc.value.details or "")

    def test_cache_hits_one_resolve_per_path(self) -> None:
        src = InMemorySource({"a": "${secret:env:K}", "b": "${secret:env:K}"})
        env = _CountingEnv({"K": "val"})
        cfg = Config.load_from([src, env])
        assert cfg.get_string("a") == "val"
        assert cfg.get_string("b") == "val"
        assert env.calls == ["K"]

    def test_get_int_resolves_and_coerces(self) -> None:
        src = InMemorySource({"port": "${secret:env:PORT}"})
        env = EnvSecretSource(getenv={"PORT": "8080"}.get)
        cfg = Config.load_from([src, env])
        assert cfg.get_int("port") == 8080


class TestLoaderRegistration:
    def test_auto_register_env_source(self) -> None:
        # Even without explicit EnvSecretSource, env scheme resolves.
        src = InMemorySource({"k": "${secret:env:VAR}"})
        cfg = Config.load_from([src])
        # Auto-registered EnvSecretSource uses os.environ.get; setting
        # via monkeypatch would normally be a fixture, here we just
        # assert the wiring exists by exercising the missing-var path.
        with pytest.raises(ConfigError) as exc:
            cfg.get_string("k")
        # Should be SECRET_UNRESOLVED (var not set), NOT
        # "no SecretSource registered" (which would mean wiring failure).
        assert exc.value.reason == ConfigErrorReason.SECRET_UNRESOLVED
        assert "not set in the process environment" in (exc.value.details or "")

    def test_explicit_env_source_overrides_default(self) -> None:
        src = InMemorySource({"k": "${secret:env:V}"})
        env = EnvSecretSource(getenv={"V": "from-explicit"}.get)
        cfg = Config.load_from([src, env])
        assert cfg.get_string("k") == "from-explicit"

    def test_duplicate_secret_source_scheme_raises(self) -> None:
        env1 = EnvSecretSource()
        env2 = EnvSecretSource()  # also scheme="env"
        with pytest.raises(ConfigError) as exc:
            Config.load_from([env1, env2])
        assert exc.value.reason == ConfigErrorReason.VALIDATION_FAILED
        assert "duplicate SecretSource scheme: 'env'" in (exc.value.details or "")


class TestPhase1BackwardsCompat:
    """`${VAR}` syntax must keep working unchanged (ADR-0002 §1.1)."""

    def test_var_syntax_still_works(self, tmp_path: object) -> None:
        # File source goes through Phase 1 raw-text interpolation.
        from pathlib import Path

        from dagstack.config import YamlFileSource

        cast_tmp_path = Path(str(tmp_path))
        cfg_file = cast_tmp_path / "test.yaml"
        cfg_file.write_text("k: ${OPENAI_KEY}\n")
        src = YamlFileSource(cfg_file, env={"OPENAI_KEY": "value-via-phase1"})
        cfg = Config.load_from([src])
        assert cfg.get_string("k") == "value-via-phase1"

    def test_var_with_default_still_works(self, tmp_path: object) -> None:
        from pathlib import Path

        from dagstack.config import YamlFileSource

        cast_tmp_path = Path(str(tmp_path))
        cfg_file = cast_tmp_path / "test.yaml"
        cfg_file.write_text("k: ${MISSING:-fb}\n")
        src = YamlFileSource(cfg_file, env={})
        cfg = Config.load_from([src])
        assert cfg.get_string("k") == "fb"

    def test_secret_env_equivalent_to_var(self, tmp_path: object) -> None:
        # The two forms are semantically identical per ADR-0002 §1.1.
        from pathlib import Path

        from dagstack.config import YamlFileSource

        cast_tmp_path = Path(str(tmp_path))
        cfg_file = cast_tmp_path / "test.yaml"
        cfg_file.write_text(
            "phase1: ${KEY}\nphase2: ${secret:env:KEY}\n",
        )
        src = YamlFileSource(cfg_file, env={"KEY": "shared-value"})
        env = EnvSecretSource(getenv={"KEY": "shared-value"}.get)
        cfg = Config.load_from([src, env])
        assert cfg.get_string("phase1") == "shared-value"
        assert cfg.get_string("phase2") == "shared-value"
