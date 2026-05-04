"""Auto-tests for code snippets from `dagstack/config-docs/site/docs/intro.mdx`.

Each test reproduces a Python snippet verbatim and then asserts the
expectations stated in its comments (`# "order-service"`, `# 20`, etc.).
On API-vs-docs drift the test fails — the author sees which page needs
to be rewritten.
"""

from __future__ import annotations

import os
from pathlib import Path
from textwrap import dedent

import pytest
from pydantic import BaseModel, Field

from dagstack.config import Config

# Fixture YAML — an exact copy of the example from intro.mdx, section
# "Installation" (the `app-config.yaml` block). When the docs change,
# update this here.
APP_CONFIG_YAML = dedent("""\
    app:
      name: "order-service"
      tagline: "Order processor"

    database:
      host: "${DB_HOST:-localhost}"
      port: "${DB_PORT:-5432}"
      name: "${DB_NAME:-orders}"
      user: "${DB_USER}"
      password: "${DB_PASSWORD}"
      pool_size: 20

    cache:
      url: "${REDIS_URL:-redis://localhost:6379/0}"
      ttl_min: 15

    api:
      host: "0.0.0.0"
      port: 8080
      request_timeout_s: 30
""")


@pytest.fixture
def app_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write app-config.yaml into a temporary directory and set the
    minimally required env variables (`DB_USER`, `DB_PASSWORD` — no
    default in YAML) so interpolation does not fail with
    `env_unresolved`.
    """
    monkeypatch.setenv("DB_USER", "app")
    monkeypatch.setenv("DB_PASSWORD", "test-pw")
    cfg_path = tmp_path / "app-config.yaml"
    cfg_path.write_text(APP_CONFIG_YAML, encoding="utf-8")
    # cd into tmp_path so that `Config.load("app-config.yaml")` from
    # the snippet resolves the relative path (the snippet in the docs
    # uses exactly that form).
    monkeypatch.chdir(tmp_path)
    return cfg_path


# ── Section "Loading and reading" ───────────────────────────────────


def test_intro__load_and_read(app_config: Path) -> None:
    """Snippet `docs/intro.mdx` → section "Loading and reading" →
    Python TabItem.
    """
    # --- snippet start -------------------------------------------------
    from dagstack.config import Config

    config = Config.load("app-config.yaml")

    # Basic accessor methods:
    print(config.get_string("app.name"))  # "order-service"
    print(config.get_int("database.pool_size"))  # 20
    print(config.get_int("api.port"))  # 8080

    # With a default — if the path is absent, the supplied value is returned:
    print(config.get_int("api.max_body_mb", default=10))  # 10
    # --- snippet end ---------------------------------------------------

    # Assertions matching the snippet's comments.
    assert config.get_string("app.name") == "order-service"
    assert config.get_int("database.pool_size") == 20
    assert config.get_int("api.port") == 8080
    assert config.get_int("api.max_body_mb", default=10) == 10


# ── Section "Typed access" ──────────────────────────────────────────


def test_intro__typed_section(app_config: Path) -> None:
    """Snippet `docs/intro.mdx` → section "Typed access" →
    Python TabItem.
    """

    # --- snippet start -------------------------------------------------
    from pydantic import BaseModel, Field

    from dagstack.config import Config

    class DatabaseConfig(BaseModel):
        host: str
        port: int = Field(5432, ge=1, le=65535)
        name: str
        user: str
        password: str = Field(..., min_length=1)
        pool_size: int = Field(20, ge=1, le=1000)

    config = Config.load("app-config.yaml")
    db = config.get_section("database", DatabaseConfig)
    # Attribute access, with validation:
    # pool = create_pool(host=db.host, port=db.port, pool_size=db.pool_size)
    #   ^^ create_pool is a user-defined function in the snippet,
    #   commented out here (not part of the binding API). The rest is
    #   verbatim.
    # --- snippet end ---------------------------------------------------

    # Validation passed and values match the YAML.
    assert db.host == "localhost"
    assert db.port == 5432
    assert db.name == "orders"
    assert db.user == "app"
    assert db.password == "test-pw"
    assert db.pool_size == 20


# Explicit references so the linter does not flag them as unused.
_ = (os, BaseModel, Field, Config)
