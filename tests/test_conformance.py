"""Conformance runner — runs golden fixtures from `spec/conformance/`.

Per spec §9.1 ADR-0001: a binding must pass every test in `manifest.yaml`
with byte-identical canonical JSON output.

Run:
    uv run pytest -m conformance -v

The runner reads `spec/conformance/manifest.yaml`; for each test entry:
1. Build an env mapping from an optional env file.
2. Create a YamlFileSource for each input.
3. Happy path (`expected` key): load + merge + serialize canonical JSON,
   diff byte-identically against `expected/<id>.json`.
4. Error case (`expected_error` key): assert a ConfigError is raised
   with matching reason + path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from dagstack.config import (
    Config,
    ConfigError,
    EnvSecretSource,
    YamlFileSource,
)
from dagstack.config.canonical_json import canonical_json_dumps
from dagstack.config.secrets import SecretRef

if TYPE_CHECKING:
    from collections.abc import Mapping

# ─── Paths ───────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent
_SPEC_ROOT = _REPO_ROOT / "spec"
_CONF_ROOT = _SPEC_ROOT / "conformance"
_MANIFEST_PATH = _CONF_ROOT / "manifest.yaml"


def _load_manifest() -> list[dict[str, Any]]:
    """Parse manifest.yaml → list of test specs (skipped when the spec submodule is absent)."""
    if not _MANIFEST_PATH.exists():
        pytest.skip(f"spec submodule not initialised: {_MANIFEST_PATH} missing")
    data = yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8"))
    tests = data.get("tests", [])
    assert isinstance(tests, list)
    return tests


_MANIFEST = _load_manifest() if _MANIFEST_PATH.exists() else []
_TEST_IDS = [str(t["id"]) for t in _MANIFEST]


# ─── Helpers ────────────────────────────────────────────────────────────────


def _load_env(env_rel_path: str | None) -> Mapping[str, str]:
    """Parse `conformance/env/<file>.env` into a dict. Blank lines and `#` comments are skipped."""
    if env_rel_path is None:
        return {}
    env_path = _CONF_ROOT / env_rel_path
    if not env_path.exists():
        pytest.fail(f"env file referenced but missing: {env_rel_path}")
    env: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        env[key.strip()] = value
    return env


def _resolve_input(rel_path: str) -> Path:
    return _CONF_ROOT / rel_path


def _read_expected_json(rel_path: str) -> str:
    """Read the expected canonical JSON (canonical — no trailing newline per spec)."""
    path = _CONF_ROOT / rel_path
    text = path.read_text(encoding="utf-8")
    # Soften: if the file content was written with a trailing newline
    # (a rare authoring slip), strip it during comparison — the wire-
    # level actual output has no newline.
    return text.rstrip("\n")


# ─── Test cases ─────────────────────────────────────────────────────────────


@pytest.mark.conformance
@pytest.mark.parametrize(
    "test_spec",
    _MANIFEST,
    ids=_TEST_IDS,
)
def test_conformance_fixture(test_spec: dict[str, Any]) -> None:
    """Run a single fixture from the manifest."""
    # v2.1: fixtures tagged `runner_extension_required` require getter /
    # getSection-level calls that the v1.0 runner does not model (only
    # load-level). Bindings MAY use these fixtures directly in their own
    # unit tests — which is what config-python does in
    # tests/test_config.py and tests/docs_examples/*.py. Skipped until
    # the runner extension lands (tracked in config-spec as follow-up).
    tags = test_spec.get("tags", []) or []
    if "runner_extension_required" in tags:
        pytest.skip(
            "getter/getSection-level fixture — runner v1.0 supports only "
            "load-level errors; biding-native unit tests cover this case"
        )

    # ADR-0002 phase2_secrets_vault — gated on a live Vault dev server.
    # Bindings opt in by setting DAGSTACK_CONFORMANCE_VAULT_ADDR; the
    # spec ships docker-compose.yml + seed.sh under conformance/vault/.
    if "phase2_secrets_vault" in tags and not os.environ.get("DAGSTACK_CONFORMANCE_VAULT_ADDR"):
        pytest.skip(
            "phase2_secrets_vault fixtures require DAGSTACK_CONFORMANCE_VAULT_ADDR "
            "(see spec/conformance/vault/docker-compose.yml + seed.sh)"
        )

    env = _load_env(test_spec.get("env"))
    inputs = [_resolve_input(p) for p in test_spec["inputs"]]
    sources = [YamlFileSource(p, env=env) for p in inputs]

    # ADR-0002 phase2_secrets — the env vector loaded above feeds the
    # EnvSecretSource (the `env` scheme), NOT just the Phase 1 ${VAR}
    # interpolator. We construct an explicit EnvSecretSource here so
    # the loader uses fixture-supplied env, not os.environ.
    extra_sources: list[Any] = []
    if "phase2_secrets" in tags:
        extra_sources.append(EnvSecretSource(getenv=env.get))
    if "phase2_secrets_vault" in tags:
        # The vault scheme — connect to the dev-mode Vault seeded by
        # spec/conformance/vault/seed.sh.
        from dagstack.config.vault import TokenAuth, VaultSource

        vault_addr = os.environ["DAGSTACK_CONFORMANCE_VAULT_ADDR"]
        vault_token = os.environ.get("DAGSTACK_CONFORMANCE_VAULT_TOKEN", "conformance-root-token")
        extra_sources.append(VaultSource(addr=vault_addr, auth=TokenAuth(token=vault_token)))

    expected = test_spec.get("expected")
    expected_error = test_spec.get("expected_error")

    if expected is not None:
        _assert_happy_path(sources, extra_sources, expected)
    elif expected_error is not None:
        _assert_error_case(sources, extra_sources, expected_error)
    else:
        pytest.fail(f"test {test_spec['id']!r}: neither `expected` nor `expected_error` provided")


def _resolved_tree(cfg: Config) -> Any:
    """Walk the merged tree, replacing every SecretRef with the resolved
    string. Conformance fixtures compare fully-resolved canonical JSON
    against `expected/<id>.json`, so we need raw string values, not the
    field-name-masked output of `Config.snapshot()`.

    Reaches into the binding's private `_tree` and `_resolve_secret_ref`
    — acceptable because this is the binding's own conformance runner
    (test-internal). Third-party adapters should use the public
    `eager_secrets=True` flag on `Config.load_from(...)` plus per-field
    `Config.get*()` calls.
    """

    def walk(value: Any, path: str = "") -> Any:
        if isinstance(value, dict):
            return {k: walk(v, f"{path}.{k}" if path else k) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(value)]
        if isinstance(value, SecretRef):
            return cfg._resolve_secret_ref(value, path=path)
        return value

    return walk(cfg._tree)


def _assert_happy_path(
    sources: list[YamlFileSource],
    extra_sources: list[Any],
    expected_rel_path: str,
) -> None:
    # `eager_secrets=True` surfaces backend errors at load time, mirroring
    # the TS / Go bindings' default eager mode for cross-binding parity.
    cfg = Config.load_from([*sources, *extra_sources], eager_secrets=True)
    merged = _resolved_tree(cfg)
    actual = canonical_json_dumps(merged)
    expected = _read_expected_json(expected_rel_path)
    assert actual == expected, f"conformance mismatch\nactual:   {actual}\nexpected: {expected}"


def _assert_error_case(
    sources: list[YamlFileSource],
    extra_sources: list[Any],
    expected_error: dict[str, Any],
) -> None:
    expected_reason = expected_error["reason"]
    expected_path = expected_error.get("path", "")

    with pytest.raises(ConfigError) as exc_info:
        Config.load_from([*sources, *extra_sources], eager_secrets=True)
    err = exc_info.value
    assert err.reason.value == expected_reason, (
        f"reason mismatch: got {err.reason.value!r}, expected {expected_reason!r}"
    )
    assert err.path == expected_path, f"path mismatch: got {err.path!r}, expected {expected_path!r}"
