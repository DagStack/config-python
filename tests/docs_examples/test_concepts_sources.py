"""Auto-tests for `docs/concepts/sources.mdx` (Python snippets)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from dagstack.config import Config, InMemorySource, YamlFileSource


def test_concepts_sources__load_from_layering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`docs/concepts/sources.mdx` → "Explicit list of sources" →
    Python TabItem.

    The snippet demonstrates `Config.load_from` with a YamlFileSource +
    InMemorySource as a test-override, where order = priority (later
    overrides earlier).
    """
    cfg_path = tmp_path / "app-config.yaml"
    cfg_path.write_text(
        dedent("""\
            database:
              host: "localhost"
              pool_size: 20
        """),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    # --- snippet start -------------------------------------------------
    from dagstack.config import Config, InMemorySource, YamlFileSource

    config = Config.load_from(
        [
            YamlFileSource("app-config.yaml"),
            InMemorySource({"database": {"pool_size": 5}}),  # test-override
        ]
    )
    # --- snippet end ---------------------------------------------------

    # Argument order = priority order: the last one wins.
    assert config.get_int("database.pool_size") == 5
    assert config.get_string("database.host") == "localhost"  # from YAML


# Explicit reference so the linter does not flag unused imports.
_ = (Config, InMemorySource, YamlFileSource)
