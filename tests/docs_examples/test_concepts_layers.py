"""Auto-tests for `docs/concepts/layers.mdx` (Python snippets)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dagstack.config import Config


def test_concepts_layers__explicit_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`docs/concepts/layers.mdx` → "Explicit list of layers" →
    Python TabItem.

    `Config.load_paths(paths[])` is an alternative to auto-discovery: an
    explicit list of files, order = priority, DAGSTACK_ENV is not
    applied.
    """
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "base.yaml").write_text(
        "database:\n  host: base-host\n  pool_size: 10\n", encoding="utf-8"
    )
    (cfg_dir / "integration-test.yaml").write_text("database:\n  pool_size: 3\n", encoding="utf-8")
    (cfg_dir / "secrets-ci.yaml").write_text("database:\n  password: ci-secret\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # --- snippet start -------------------------------------------------
    config = Config.load_paths(
        [
            "config/base.yaml",
            "config/integration-test.yaml",
            "config/secrets-ci.yaml",
        ]
    )
    # No DAGSTACK_ENV logic; order defines priority.
    # --- snippet end ---------------------------------------------------

    # Priority: integration-test overrode pool_size, secrets-ci added
    # password, host from base is preserved.
    assert config.get_string("database.host") == "base-host"
    assert config.get_int("database.pool_size") == 3
    assert config.get_string("database.password") == "ci-secret"


def test_concepts_layers__source_ids_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`docs/concepts/layers.mdx` → "How to see which layers applied" →
    Python TabItem. The `source_ids()` method returns the list of source
    identifiers in load order.
    """
    base = tmp_path / "app-config.yaml"
    base.write_text("only: me\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Disable the env layer so source_ids only contains base.
    monkeypatch.delenv("DAGSTACK_ENV", raising=False)

    config = Config.load("app-config.yaml")

    # --- snippet start -------------------------------------------------
    print(config.source_ids())
    # → ["yaml:app-config.yaml", "yaml:app-config.local.yaml",
    #    "yaml:app-config.production.yaml"]
    # --- snippet end ---------------------------------------------------

    # In this test only base, no local/production.
    ids = config.source_ids()
    assert len(ids) == 1
    assert ids[0].startswith("yaml:")
    assert "app-config.yaml" in ids[0]
