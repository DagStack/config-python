"""Auto-tests for `docs/guides/declaring-section.mdx` (Python snippets)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from pydantic import BaseModel, Field

from dagstack.config import Config


# Standard DatabaseConfig used by many snippets across the docs.
class DatabaseConfig(BaseModel):
    host: str
    port: int = 5432
    name: str
    user: str
    password: str
    pool_size: int = 20
    ssl: bool = False


# A separate section for the "Isolation" snippet so that the CacheConfig
# snippet does not require domain-specific fields.
class CacheConfig(BaseModel):
    url: str
    ttl_min: int = 15


@pytest.fixture
def full_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "app-config.yaml"
    cfg.write_text(
        dedent("""\
            database:
              host: localhost
              port: 5432
              name: orders
              user: app
              password: test-pw
              pool_size: 20

            cache:
              url: redis://localhost:6379/0
              ttl_min: 15
        """),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return cfg


# ── Step 3 "Read the section" ────────────────────────────────────────


def test_declaring_section__get_section(full_config: Path) -> None:
    """Python TabItem, section "Step 3. Read the section"."""

    # --- snippet start -------------------------------------------------
    from dagstack.config import Config

    config = Config.load("app-config.yaml")
    db_cfg = config.get_section("database", DatabaseConfig)
    # db_cfg is a DatabaseConfig instance, already validated.
    # --- snippet end ---------------------------------------------------

    assert isinstance(db_cfg, DatabaseConfig)
    assert db_cfg.host == "localhost"
    assert db_cfg.pool_size == 20


# ── Step 4 "Isolation" ───────────────────────────────────────────────


def test_declaring_section__isolation_correct(full_config: Path) -> None:
    """Python TabItem, section "Step 4. Isolation"."""
    config = Config.load("app-config.yaml")

    # --- snippet start -------------------------------------------------
    # Correct — in the database service:
    db_cfg = config.get_section("database", DatabaseConfig)

    # Incorrect — the database service reads someone else's section:
    cache_cfg = config.get_section("cache", CacheConfig)
    # The database service now depends on the structure of `cache`.
    # --- snippet end ---------------------------------------------------

    # Both calls work — the docs simply caution against cross-section
    # reads; the API does not forbid them.
    assert db_cfg.host == "localhost"
    assert cache_cfg.url == "redis://localhost:6379/0"


# ── Step 5 "Defaults in schema" ──────────────────────────────────────


def test_declaring_section__defaults_in_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python TabItem, section "Step 5. Default values".

    Optional fields are declared with default values in the schema —
    when absent from YAML they are substituted without a fallback in
    code.
    """
    # YAML contains only the required fields.
    cfg = tmp_path / "app-config.yaml"
    cfg.write_text(
        "database:\n  host: localhost\n  user: app\n  password: pw\n  name: test\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    # --- snippet start (defaults-in-schema) ----------------------------
    class DatabaseConfig(BaseModel):
        host: str  # required
        user: str  # required
        password: str  # required
        port: int = 5432  # schema default
        pool_size: int = 20  # schema default
        ssl: bool = False  # schema default

    # --- snippet end ---------------------------------------------------

    # We need name (required) — add it via a separate field. The docs
    # snippet does not mention name, but the YAML test needs it.
    class DatabaseConfigWithName(DatabaseConfig):
        name: str

    config = Config.load("app-config.yaml")
    db = config.get_section("database", DatabaseConfigWithName)

    # Defaults applied:
    assert db.port == 5432
    assert db.pool_size == 20
    assert db.ssl is False


# Explicit reference to Field (used in the docs, here only indirectly).
_ = Field
