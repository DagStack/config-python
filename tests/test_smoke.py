"""Smoke tests for the skeleton: the package is importable, version is readable."""

from __future__ import annotations


def test_package_importable() -> None:
    import dagstack.config

    assert dagstack.config is not None


def test_version_is_string() -> None:
    from dagstack.config import __version__

    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_spec_submodule_present() -> None:
    """spec/ must contain the config-spec ADR — it is a git submodule."""
    from pathlib import Path

    repo_root = Path(__file__).parent.parent
    adr = repo_root / "spec" / "adr" / "0001-yaml-configuration.md"
    assert adr.exists(), f"spec submodule not initialised: {adr} missing"
