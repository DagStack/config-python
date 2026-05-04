"""Unit tests for the Config public API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from dagstack.config import Config, ConfigError, ConfigErrorReason, InMemorySource

if TYPE_CHECKING:
    from pathlib import Path

# ─── Fixtures ───────────────────────────────────────────────────────────────


def _config(tree: dict[str, object]) -> Config:
    """Shortcut — Config from an InMemorySource."""
    return Config.load_from([InMemorySource(tree)])


# ─── has / get (raw) ────────────────────────────────────────────────────────


class TestHas:
    def test_existing_scalar(self) -> None:
        cfg = _config({"a": 1})
        assert cfg.has("a") is True

    def test_nested_existing(self) -> None:
        cfg = _config({"a": {"b": 1}})
        assert cfg.has("a.b") is True

    def test_missing_key(self) -> None:
        cfg = _config({"a": 1})
        assert cfg.has("b") is False

    def test_missing_nested_key(self) -> None:
        cfg = _config({"a": {}})
        assert cfg.has("a.b") is False

    def test_type_mismatch_in_middle_returns_false(self) -> None:
        # `a` is scalar; `a.b` cannot navigate → TYPE_MISMATCH → has returns False.
        cfg = _config({"a": 42})
        assert cfg.has("a.b") is False

    def test_array_index_existing(self) -> None:
        cfg = _config({"items": [1, 2, 3]})
        assert cfg.has("items[0]") is True
        assert cfg.has("items[5]") is False


class TestGetRaw:
    def test_returns_raw_value(self) -> None:
        cfg = _config({"a": {"b": [1, 2, 3]}})
        assert cfg.get("a.b") == [1, 2, 3]

    def test_returns_scalar(self) -> None:
        cfg = _config({"x": "hello"})
        assert cfg.get("x") == "hello"

    def test_missing_raises(self) -> None:
        cfg = _config({})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get("x")
        assert exc_info.value.reason is ConfigErrorReason.MISSING

    def test_default_used_when_missing(self) -> None:
        cfg = _config({})
        assert cfg.get("x", default="fallback") == "fallback"


# ─── get_string / get_int / get_number / get_bool ─────────────────────────


class TestGetString:
    def test_plain_string(self) -> None:
        cfg = _config({"x": "hello"})
        assert cfg.get_string("x") == "hello"

    def test_int_strict_raises(self) -> None:
        # v0.2.0 breaking: get_string strict per ADR v2.1 §4.3.
        # In v0.1.0 there was a coerce `42 → "42"`.
        cfg = _config({"x": 42})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_string("x")
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH

    def test_float_strict_raises(self) -> None:
        # v0.2.0 breaking. In v0.1.0 it was `1.5 → "1.5"`.
        cfg = _config({"x": 1.5})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_string("x")
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH

    def test_bool_strict_raises(self) -> None:
        # v0.2.0 breaking. In v0.1.0 it was `True → "true"`.
        cfg = _config({"x": True})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_string("x")
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH

    def test_missing_with_default(self) -> None:
        cfg = _config({})
        assert cfg.get_string("absent", default="fallback") == "fallback"

    def test_missing_without_default_raises(self) -> None:
        cfg = _config({})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_string("x")
        assert exc_info.value.reason is ConfigErrorReason.MISSING

    def test_list_raises_type_mismatch(self) -> None:
        cfg = _config({"x": [1, 2]})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_string("x")
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH

    def test_dict_raises_type_mismatch(self) -> None:
        cfg = _config({"x": {"nested": 1}})
        with pytest.raises(ConfigError):
            cfg.get_string("x")


class TestGetInt:
    def test_native_int(self) -> None:
        cfg = _config({"x": 42})
        assert cfg.get_int("x") == 42

    def test_string_numeric(self) -> None:
        cfg = _config({"x": "42"})
        assert cfg.get_int("x") == 42

    def test_negative_string(self) -> None:
        cfg = _config({"x": "-7"})
        assert cfg.get_int("x") == -7

    def test_bool_rejected(self) -> None:
        # `True` in Python is `int(1)`, but the spec requires strict coercion.
        cfg = _config({"x": True})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_int("x")
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH

    def test_fractional_float_rejected(self) -> None:
        cfg = _config({"x": 1.5})
        with pytest.raises(ConfigError):
            cfg.get_int("x")

    def test_whole_number_float_in_safe_range_accepted(self) -> None:
        # v0.2.0: whole-number float in the i-JSON safe range → int
        # (v2.1 §4.3). Parity with Go + required for JSON sources.
        cfg = _config({"x": 100.0, "y": -42.0, "zero": 0.0, "neg_zero": -0.0})
        assert cfg.get_int("x") == 100
        assert cfg.get_int("y") == -42
        assert cfg.get_int("zero") == 0
        assert cfg.get_int("neg_zero") == 0

    def test_whole_number_float_outside_safe_range_rejected(self) -> None:
        # Beyond ±(2^53-1) precision is not guaranteed → reject.
        cfg = _config({"x": 2.0**60})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_int("x")
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH

    def test_non_numeric_string_rejected(self) -> None:
        cfg = _config({"x": "abc"})
        with pytest.raises(ConfigError):
            cfg.get_int("x")

    def test_missing_with_default(self) -> None:
        cfg = _config({})
        assert cfg.get_int("x", default=100) == 100

    def test_missing_without_default_raises(self) -> None:
        cfg = _config({})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_int("x")
        assert exc_info.value.reason is ConfigErrorReason.MISSING

    def test_list_rejected(self) -> None:
        cfg = _config({"x": [1, 2]})
        with pytest.raises(ConfigError):
            cfg.get_int("x")


class TestGetNumber:
    def test_native_float(self) -> None:
        cfg = _config({"x": 1.5})
        assert cfg.get_number("x") == 1.5

    def test_native_int_coerced(self) -> None:
        cfg = _config({"x": 42})
        assert cfg.get_number("x") == 42.0

    def test_numeric_string(self) -> None:
        cfg = _config({"x": "3.14"})
        assert cfg.get_number("x") == pytest.approx(3.14)

    def test_integer_string(self) -> None:
        cfg = _config({"x": "42"})
        assert cfg.get_number("x") == 42.0

    def test_bool_rejected(self) -> None:
        cfg = _config({"x": True})
        with pytest.raises(ConfigError):
            cfg.get_number("x")

    def test_non_numeric_string_rejected(self) -> None:
        cfg = _config({"x": "abc"})
        with pytest.raises(ConfigError):
            cfg.get_number("x")

    def test_missing_without_default_raises(self) -> None:
        cfg = _config({})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_number("x")
        assert exc_info.value.reason is ConfigErrorReason.MISSING

    def test_list_rejected(self) -> None:
        cfg = _config({"x": [1, 2]})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_number("x")
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH


class TestGetBool:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, True),
            (False, False),
            ("true", True),
            ("TRUE", True),
            ("yes", True),
            ("1", True),
            ("false", False),
            ("no", False),
            ("0", False),
        ],
    )
    def test_truthy_falsy(self, value: object, expected: bool) -> None:
        cfg = _config({"x": value})
        assert cfg.get_bool("x") is expected

    def test_invalid_string_rejected(self) -> None:
        cfg = _config({"x": "maybe"})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_bool("x")
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH

    def test_int_rejected(self) -> None:
        # Plain int — not bool-coercible (strict).
        cfg = _config({"x": 1})
        with pytest.raises(ConfigError):
            cfg.get_bool("x")

    def test_missing_with_default(self) -> None:
        cfg = _config({})
        assert cfg.get_bool("x", default=True) is True

    def test_missing_without_default_raises(self) -> None:
        cfg = _config({})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_bool("x")
        assert exc_info.value.reason is ConfigErrorReason.MISSING

    def test_list_rejected(self) -> None:
        cfg = _config({"x": [1, 2]})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_bool("x")
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH


class TestGetList:
    def test_list_returned(self) -> None:
        cfg = _config({"x": [1, 2, 3]})
        assert cfg.get_list("x") == [1, 2, 3]

    def test_not_a_list_raises(self) -> None:
        cfg = _config({"x": "abc"})
        with pytest.raises(ConfigError):
            cfg.get_list("x")

    def test_returned_list_is_copy(self) -> None:
        # Mutating the returned list does not affect the internal tree.
        cfg = _config({"x": [1, 2]})
        lst = cfg.get_list("x")
        lst.append(999)
        assert cfg.get_list("x") == [1, 2]


# ─── get_section (Pydantic) ─────────────────────────────────────────────────


class _DatabaseSchema(BaseModel):
    name: str
    pool_size: int = 20
    timeout_ms: int | None = None


class TestGetSection:
    def test_valid_section_parsed(self) -> None:
        cfg = _config({"database": {"name": "primary", "pool_size": 30}})
        db = cfg.get_section("database", _DatabaseSchema)
        assert db.name == "primary"
        assert db.pool_size == 30
        assert db.timeout_ms is None  # default

    def test_defaults_applied(self) -> None:
        cfg = _config({"database": {"name": "primary"}})
        db = cfg.get_section("database", _DatabaseSchema)
        assert db.pool_size == 20

    def test_missing_required_field_raises_validation(self) -> None:
        cfg = _config({"database": {}})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_section("database", _DatabaseSchema)
        assert exc_info.value.reason is ConfigErrorReason.VALIDATION_FAILED

    def test_native_int_into_string_field_raises_type_mismatch(self) -> None:
        # ADR-0001 v2.1 §4.4 reverse case: a native int/float/bool in a
        # string field → type_mismatch (mirror of §4.3 getString strict
        # mode), guarding against silent `dimension: 768` → `"768"`.
        cfg = _config({"database": {"name": 42}})  # name must be str
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_section("database", _DatabaseSchema)
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH
        # v2.1 §4.5 Path preservation: full path section.field.
        assert exc_info.value.path == "database.name"

    def test_section_not_a_mapping(self) -> None:
        cfg = _config({"database": "not-a-dict"})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_section("database", _DatabaseSchema)
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH

    def test_missing_section(self) -> None:
        cfg = _config({})
        with pytest.raises(ConfigError) as exc_info:
            cfg.get_section("database", _DatabaseSchema)
        assert exc_info.value.reason is ConfigErrorReason.MISSING


# ─── Subscriptions (Phase 1: inactive) ──────────────────────────────────────


class TestSubscriptionsInactive:
    def test_on_change_returns_inactive(self, caplog: object) -> None:
        cfg = _config({"a": 1})
        sub = cfg.on_change("a", callback=lambda _: None)
        assert sub.active is False
        assert sub.inactive_reason == "no watch-capable source registered"

    def test_on_section_change_returns_inactive(self) -> None:
        cfg = _config({"database": {"name": "primary"}})
        sub = cfg.on_section_change("database", _DatabaseSchema, callback=lambda _old, _new: None)
        assert sub.active is False

    def test_warning_emitted_on_inactive_subscription(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cfg = _config({"a": 1})
        with caplog.at_level(logging.WARNING, logger="dagstack.config.internal"):
            cfg.on_change("a", callback=lambda _: None)
        assert any("subscription_without_watch" in record.message for record in caplog.records)

    def test_reload_is_noop(self) -> None:
        cfg = _config({"a": 1})
        # Just shouldn't raise.
        cfg.reload()


# ─── Constructors ───────────────────────────────────────────────────────────


class TestLoadFileBased:
    def test_load_single_file(self, tmp_path: Path) -> None:
        f = tmp_path / "app.yaml"
        f.write_text("database:\n  name: primary\n", encoding="utf-8")
        cfg = Config.load(f)
        assert cfg.get_string("database.name") == "primary"

    def test_load_picks_up_local_override(self, tmp_path: Path) -> None:
        base = tmp_path / "app.yaml"
        base.write_text("database:\n  name: primary\n  pool_size: 20\n", encoding="utf-8")
        local = tmp_path / "app.local.yaml"
        local.write_text("database:\n  pool_size: 30\n", encoding="utf-8")
        cfg = Config.load(base)
        assert cfg.get_string("database.name") == "primary"
        assert cfg.get_int("database.pool_size") == 30

    def test_load_picks_up_env_override(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        base = tmp_path / "app.yaml"
        base.write_text("database:\n  name: primary\n", encoding="utf-8")
        prod = tmp_path / "app.production.yaml"
        prod.write_text("database:\n  name: replica\n", encoding="utf-8")
        monkeypatch.setenv("DAGSTACK_ENV", "production")
        cfg = Config.load(base)
        assert cfg.get_string("database.name") == "replica"

    def test_load_all_layers_combined(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        base = tmp_path / "app.yaml"
        base.write_text("a: 1\nb: 2\nc: 3\n", encoding="utf-8")
        (tmp_path / "app.local.yaml").write_text("b: 20\n", encoding="utf-8")
        (tmp_path / "app.production.yaml").write_text("c: 300\n", encoding="utf-8")
        monkeypatch.setenv("DAGSTACK_ENV", "production")
        cfg = Config.load(base)
        assert cfg.get_int("a") == 1
        assert cfg.get_int("b") == 20
        assert cfg.get_int("c") == 300

    def test_load_paths_explicit(self, tmp_path: Path) -> None:
        f1 = tmp_path / "base.yaml"
        f1.write_text("a: 1\nb: 2\n", encoding="utf-8")
        f2 = tmp_path / "override.yaml"
        f2.write_text("b: 20\n", encoding="utf-8")
        cfg = Config.load_paths([f1, f2])
        assert cfg.get_int("a") == 1
        assert cfg.get_int("b") == 20

    def test_load_from_sources(self) -> None:
        cfg = Config.load_from([InMemorySource({"a": 1, "b": 2}), InMemorySource({"b": 20})])
        assert cfg.get_int("b") == 20


class TestSourceIds:
    # ADR-0001 v2.1 §4.1: source_ids is a method, not a property
    # (cross-binding parity with TS sourceIds() and Go SourceIDs()).

    def test_source_ids_recorded(self) -> None:
        s1 = InMemorySource({"a": 1}, source_id="test:s1")
        s2 = InMemorySource({"b": 2}, source_id="test:s2")
        cfg = Config.load_from([s1, s2])
        assert cfg.source_ids() == ["test:s1", "test:s2"]

    def test_source_ids_is_copy(self) -> None:
        cfg = Config.load_from([InMemorySource({}, source_id="x")])
        ids = cfg.source_ids()
        ids.append("mutation")
        assert cfg.source_ids() == ["x"]
