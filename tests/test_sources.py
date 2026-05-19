"""Unit tests for ConfigSource implementations."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from dagstack.config.errors import ConfigError, ConfigErrorReason
from dagstack.config.sources import (
    ConfigSource,
    InMemorySource,
    JsonFileSource,
    YamlFileSource,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestYamlFileSource:
    def test_simple_file_loaded(self, tmp_path: Path) -> None:
        file = tmp_path / "config.yaml"
        file.write_text("llm:\n  model: gpt-4\n  temperature: 0.7\n", encoding="utf-8")
        source = YamlFileSource(file)
        tree = source.load()
        assert tree == {"llm": {"model": "gpt-4", "temperature": 0.7}}

    def test_source_id_format(self, tmp_path: Path) -> None:
        file = tmp_path / "app.yaml"
        file.write_text("{}", encoding="utf-8")
        source = YamlFileSource(file)
        assert source.id == f"yaml:{file}"

    def test_interpolate_flag_true(self, tmp_path: Path) -> None:
        file = tmp_path / "c.yaml"
        file.write_text("{}", encoding="utf-8")
        source = YamlFileSource(file)
        assert source.interpolate is True

    def test_env_interpolation_applied(self, tmp_path: Path) -> None:
        file = tmp_path / "c.yaml"
        file.write_text(
            "llm:\n  base_url: ${OPENAI_BASE_URL:-http://localhost:11434/v1}\n",
            encoding="utf-8",
        )
        source = YamlFileSource(file, env={"OPENAI_BASE_URL": "https://api.example.com"})
        assert source.load() == {"llm": {"base_url": "https://api.example.com"}}

    def test_env_default_used_when_var_missing(self, tmp_path: Path) -> None:
        file = tmp_path / "c.yaml"
        file.write_text("url: ${MISSING:-http://default}\n", encoding="utf-8")
        source = YamlFileSource(file, env={})
        assert source.load() == {"url": "http://default"}

    def test_interpolated_int_coerced_by_yaml(self, tmp_path: Path) -> None:
        # Interpolation before parse → the YAML parser coerces "768" to int.
        file = tmp_path / "c.yaml"
        file.write_text("dimension: ${DIM}\n", encoding="utf-8")
        source = YamlFileSource(file, env={"DIM": "768"})
        assert source.load() == {"dimension": 768}

    def test_unresolved_env_raises(self, tmp_path: Path) -> None:
        file = tmp_path / "c.yaml"
        file.write_text("key: ${MISSING}\n", encoding="utf-8")
        source = YamlFileSource(file, env={})
        with pytest.raises(ConfigError) as exc_info:
            source.load()
        err = exc_info.value
        assert err.reason is ConfigErrorReason.ENV_UNRESOLVED
        assert err.source_id == source.id

    def test_file_not_found_raises_source_unavailable(self, tmp_path: Path) -> None:
        source = YamlFileSource(tmp_path / "nonexistent.yaml")
        with pytest.raises(ConfigError) as exc_info:
            source.load()
        assert exc_info.value.reason is ConfigErrorReason.SOURCE_UNAVAILABLE

    def test_invalid_yaml_raises_parse_error(self, tmp_path: Path) -> None:
        file = tmp_path / "bad.yaml"
        file.write_text("key: [unclosed\n", encoding="utf-8")
        source = YamlFileSource(file)
        with pytest.raises(ConfigError) as exc_info:
            source.load()
        assert exc_info.value.reason is ConfigErrorReason.PARSE_ERROR

    def test_root_must_be_mapping(self, tmp_path: Path) -> None:
        file = tmp_path / "c.yaml"
        file.write_text("- 1\n- 2\n", encoding="utf-8")
        source = YamlFileSource(file)
        with pytest.raises(ConfigError) as exc_info:
            source.load()
        assert exc_info.value.reason is ConfigErrorReason.PARSE_ERROR
        assert "mapping" in exc_info.value.details

    def test_empty_file_returns_empty_dict(self, tmp_path: Path) -> None:
        file = tmp_path / "empty.yaml"
        file.write_text("", encoding="utf-8")
        source = YamlFileSource(file)
        assert source.load() == {}

    def test_protocol_compliance(self, tmp_path: Path) -> None:
        file = tmp_path / "c.yaml"
        file.write_text("{}", encoding="utf-8")
        source = YamlFileSource(file)
        assert isinstance(source, ConfigSource)

    def test_whole_number_float_normalized_to_int(self, tmp_path: Path) -> None:
        # v0.2.0: parity between YAML and JSON sources — whole-number
        # float in the safe range → int. PyYAML returns float for `100.0`
        # by default; regression guard against v0.1 → v0.2 divergence.
        file = tmp_path / "nums.yaml"
        # `1.0e+100` — PyYAML requires an explicit exponent sign (YAML
        # 1.2 stance). This is a whole-number float outside the i-JSON
        # safe range — it must remain `float`; normalization does not
        # apply.
        file.write_text(
            "port: 8080.0\nratio: 0.75\nzero: -0.0\nhuge: 1.0e+100\n",
            encoding="utf-8",
        )
        tree = YamlFileSource(file).load()
        assert tree["port"] == 8080
        assert isinstance(tree["port"], int)
        assert tree["ratio"] == 0.75  # fractional preserved
        assert isinstance(tree["ratio"], float)
        assert tree["zero"] == 0
        assert isinstance(tree["zero"], int)
        # out-of-range: preserved as float
        assert isinstance(tree["huge"], float)


class TestJsonFileSource:
    def test_simple_file_loaded(self, tmp_path: Path) -> None:
        file = tmp_path / "config.json"
        file.write_text(json.dumps({"llm": {"model": "gpt-4"}}), encoding="utf-8")
        source = JsonFileSource(file)
        assert source.load() == {"llm": {"model": "gpt-4"}}

    def test_source_id_prefix(self, tmp_path: Path) -> None:
        file = tmp_path / "c.json"
        file.write_text("{}", encoding="utf-8")
        source = JsonFileSource(file)
        assert source.id == f"json:{file}"

    def test_env_interpolation_works(self, tmp_path: Path) -> None:
        file = tmp_path / "c.json"
        file.write_text('{"host": "${HOST}"}', encoding="utf-8")
        source = JsonFileSource(file, env={"HOST": "example.com"})
        assert source.load() == {"host": "example.com"}

    def test_invalid_json_raises_parse_error(self, tmp_path: Path) -> None:
        file = tmp_path / "bad.json"
        file.write_text('{"unterminated', encoding="utf-8")
        source = JsonFileSource(file)
        with pytest.raises(ConfigError) as exc_info:
            source.load()
        assert exc_info.value.reason is ConfigErrorReason.PARSE_ERROR

    def test_file_not_found(self, tmp_path: Path) -> None:
        source = JsonFileSource(tmp_path / "nope.json")
        with pytest.raises(ConfigError) as exc_info:
            source.load()
        assert exc_info.value.reason is ConfigErrorReason.SOURCE_UNAVAILABLE

    def test_whole_number_float_normalized_to_int(self, tmp_path: Path) -> None:
        # v0.2.0: JSON-source whole-number float → int in the safe
        # range. Regression guard on parity with YamlFileSource.
        file = tmp_path / "nums.json"
        file.write_text(
            '{"port": 8080.0, "ratio": 0.75, "zero": -0.0}',
            encoding="utf-8",
        )
        tree = JsonFileSource(file).load()
        assert tree["port"] == 8080
        assert isinstance(tree["port"], int)
        assert isinstance(tree["ratio"], float)
        assert tree["zero"] == 0
        assert isinstance(tree["zero"], int)


class TestInMemorySource:
    def test_returns_given_tree(self) -> None:
        tree = {"a": {"b": 1}}
        source = InMemorySource(tree)
        assert source.load() == {"a": {"b": 1}}

    def test_custom_source_id(self) -> None:
        source = InMemorySource({}, source_id="test:mock")
        assert source.id == "test:mock"

    def test_default_source_id(self) -> None:
        source = InMemorySource({})
        assert source.id == "in-memory"

    def test_interpolate_flag_false_by_default(self) -> None:
        source = InMemorySource({})
        assert source.interpolate is False

    def test_shallow_copy_returned(self) -> None:
        tree = {"a": 1}
        source = InMemorySource(tree)
        returned = source.load()
        returned["b"] = 2
        assert "b" not in tree

    def test_protocol_compliance(self) -> None:
        source = InMemorySource({})
        assert isinstance(source, ConfigSource)
