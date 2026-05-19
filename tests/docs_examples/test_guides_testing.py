"""Auto-tests for `docs/guides/testing.mdx` (Python snippets)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from pydantic import BaseModel

from dagstack.config import Config, InMemorySource


class DatabaseConfig(BaseModel):
    host: str
    port: int = 5432
    name: str
    user: str
    password: str
    pool_size: int = 20


# ── "Unit tests — inline config via an in-memory source" ────────────


def test_testing__inmemory_source_configured_size() -> None:
    """Python TabItem of the first Tabs block."""

    # --- snippet start -------------------------------------------------
    from dagstack.config import Config, InMemorySource

    config = Config.load_from(
        [
            InMemorySource(
                {
                    "database": {
                        "host": "localhost",
                        "port": 5432,
                        "name": "test",
                        "user": "app",
                        "password": "test-pw",
                        "pool_size": 42,
                    },
                }
            ),
        ]
    )
    # pool = DatabasePool(config.get_section("database", DatabaseConfig))
    #   ^^ user-defined DatabasePool — outside the binding API. Instead
    #      of asserting on pool.size we verify schema validation.
    # --- snippet end ---------------------------------------------------

    db = config.get_section("database", DatabaseConfig)
    assert db.pool_size == 42


# ── "YamlFileSource in tmpdir" ──────────────────────────────────────


def test_testing__env_interpolation_tmpdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Python TabItem of the second Tabs block — env interpolation via
    a tmp fixture."""

    # --- snippet start -------------------------------------------------
    # Env variables used for interpolation:
    monkeypatch.setenv("DB_PASSWORD", "test-pw")

    yaml_path = tmp_path / "app-config.yaml"
    yaml_path.write_text(
        dedent("""
        database:
          host: "${DB_HOST:-localhost}"
          password: "${DB_PASSWORD}"
          name: "test_db"
          user: "app"
          pool_size: 5
    """)
    )

    config = Config.load(str(yaml_path))
    assert config.get_string("database.password") == "test-pw"
    assert config.get_string("database.host") == "localhost"
    # --- snippet end ---------------------------------------------------


# ── "Integration tests with DAGSTACK_ENV" ───────────────────────────


def test_testing__production_overrides_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python snippet, section "Integration tests with DAGSTACK_ENV"."""

    # --- snippet start -------------------------------------------------
    (tmp_path / "app-config.yaml").write_text(
        dedent("""
    database:
      pool_size: 20
      host: "localhost"
      name: "test"
      user: "app"
      password: "pw"
    """)
    )
    (tmp_path / "app-config.production.yaml").write_text(
        dedent("""
    database:
      pool_size: 100
    """)
    )

    monkeypatch.setenv("DAGSTACK_ENV", "production")
    monkeypatch.chdir(tmp_path)
    config = Config.load("app-config.yaml")
    assert config.get_int("database.pool_size") == 100
    # --- snippet end ---------------------------------------------------


_ = InMemorySource  # used via symbol resolution inside snippet
