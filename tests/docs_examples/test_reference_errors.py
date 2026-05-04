"""Auto-tests for `docs/reference/errors.mdx` (Python snippets)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from dagstack.config import Config, ConfigError, ConfigErrorReason, InMemorySource


class DatabaseConfig(BaseModel):
    host: str
    password: str = Field(..., min_length=1)


@pytest.fixture
def sample_config() -> Config:
    """Config with a minimal section for negative tests."""
    return Config.load_from(
        [
            InMemorySource(
                {
                    "database": {
                        "host": "localhost",
                        "pool_size": "twenty",  # invalid for get_int
                        "password": "",  # invalid for validation
                    }
                }
            )
        ]
    )


# ── `missing` ────────────────────────────────────────────────────────


def test_errors__missing(sample_config: Config) -> None:
    """Docs snippet: ConfigError(path="nonexistent.path", reason="missing", ...)"""
    with pytest.raises(ConfigError) as excinfo:
        # --- snippet start -----------------------------------------------
        sample_config.get_string("nonexistent.path")
        # ConfigError(
        #   path="nonexistent.path",
        #   reason="missing",
        #   details="Key 'nonexistent.path' not found in config and no default provided",
        # )
        # --- snippet end -------------------------------------------------
    err = excinfo.value
    # ADR-0001 v2.1 §4.5 Path preservation: full dot-notation path.
    assert err.path == "nonexistent.path"
    assert err.reason == ConfigErrorReason.MISSING


# ── `type_mismatch` ──────────────────────────────────────────────────


def test_errors__type_mismatch(sample_config: Config) -> None:
    """Docs snippet: ConfigError(path="database.pool_size", reason="type_mismatch", ...)"""
    # YAML: pool_size: "twenty"
    with pytest.raises(ConfigError) as excinfo:
        # --- snippet start -----------------------------------------------
        sample_config.get_int("database.pool_size")
        # ConfigError(
        #   path="database.pool_size",
        #   reason="type_mismatch",
        #   details="Expected int, got string 'twenty' (does not match ^-?\\d+$)",
        # )
        # --- snippet end -------------------------------------------------
    err = excinfo.value
    assert err.path == "database.pool_size"
    assert err.reason == ConfigErrorReason.TYPE_MISMATCH


# ── `env_unresolved` ─────────────────────────────────────────────────


def test_errors__env_unresolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Docs snippet: `${DB_PASSWORD}` without a default + env missing."""
    cfg = tmp_path / "app-config.yaml"
    cfg.write_text('database:\n  password: "${DB_PASSWORD}"\n', encoding="utf-8")
    # Remove DB_PASSWORD from the environment if it was set.
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError) as excinfo:
        Config.load("app-config.yaml")

    err = excinfo.value
    assert err.reason == ConfigErrorReason.ENV_UNRESOLVED


# ── `validation_failed` ──────────────────────────────────────────────


def test_errors__validation_failed(sample_config: Config) -> None:
    """Docs snippet: get_section failed pydantic validation."""

    # --- snippet start ---------------------------------------------------
    # class DatabaseConfig(BaseModel):
    #     host: str
    #     password: str = Field(..., min_length=1)
    #
    # YAML: password: ""  (empty string)
    # --- snippet end -----------------------------------------------------
    with pytest.raises(ConfigError) as excinfo:
        sample_config.get_section("database", DatabaseConfig)

    err = excinfo.value
    assert err.reason == ConfigErrorReason.VALIDATION_FAILED


# ── `source_unavailable` ─────────────────────────────────────────────


def test_errors__source_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Docs snippet: Config.load on a non-existent file."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError) as excinfo:
        # --- snippet start -----------------------------------------------
        # The file does not exist:
        Config.load("non-existent.yaml")
        # --- snippet end -------------------------------------------------

    err = excinfo.value
    assert err.reason == ConfigErrorReason.SOURCE_UNAVAILABLE


# ── Handler snippet with try/except ──────────────────────────────────


def test_errors__handler_switch(sample_config: Config) -> None:
    """Docs snippet: try/except ConfigError + switch on reason."""
    # Simplified handler version: extract the reason from the error.
    # --- snippet start (simplified) ------------------------------------
    try:
        sample_config.get_string("nonexistent")
        handled_reason = None
    except ConfigError as exc:
        handled_reason = exc.reason
    # --- snippet end ---------------------------------------------------

    assert handled_reason == ConfigErrorReason.MISSING


_ = Field  # import used by the snippet schema
