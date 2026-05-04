"""Unit tests for path navigation."""

from __future__ import annotations

import pytest

from dagstack.config.errors import ConfigError, ConfigErrorReason
from dagstack.config.paths import navigate, parse_path


class TestParsePath:
    def test_single_key(self) -> None:
        assert parse_path("foo") == ["foo"]

    def test_dotted_path(self) -> None:
        assert parse_path("a.b.c") == ["a", "b", "c"]

    def test_with_array_index(self) -> None:
        assert parse_path("plugins[0]") == ["plugins", 0]

    def test_array_of_objects(self) -> None:
        assert parse_path("plugins[0].name") == ["plugins", 0, "name"]

    def test_nested_array_indices(self) -> None:
        assert parse_path("matrix[0][1]") == ["matrix", 0, 1]

    def test_leading_array_index(self) -> None:
        # Unusual but valid per regex: root path = list, indexed directly.
        assert parse_path("[0]") == [0]

    def test_underscores_and_digits_in_keys(self) -> None:
        assert parse_path("rag_v2.search.top_k") == ["rag_v2", "search", "top_k"]

    def test_empty_path_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            parse_path("")

    def test_dangling_dot_ignored(self) -> None:
        # Trailing dot has no semantic meaning, it is simply skipped.
        assert parse_path("a.") == ["a"]


class TestNavigateScalar:
    def test_top_level_string(self) -> None:
        assert navigate({"a": "x"}, "a") == "x"

    def test_top_level_int(self) -> None:
        assert navigate({"a": 42}, "a") == 42

    def test_top_level_none(self) -> None:
        assert navigate({"a": None}, "a") is None


class TestNavigateNested:
    def test_two_levels(self) -> None:
        assert navigate({"a": {"b": 1}}, "a.b") == 1

    def test_four_levels(self) -> None:
        tree = {"a": {"b": {"c": {"d": "deep"}}}}
        assert navigate(tree, "a.b.c.d") == "deep"


class TestNavigateArrays:
    def test_top_level_array_index(self) -> None:
        assert navigate({"items": ["x", "y", "z"]}, "items[1]") == "y"

    def test_nested_array_access(self) -> None:
        tree = {"plugins": [{"name": "A"}, {"name": "B"}]}
        assert navigate(tree, "plugins[1].name") == "B"

    def test_array_of_arrays(self) -> None:
        tree = {"matrix": [[1, 2], [3, 4]]}
        assert navigate(tree, "matrix[0][1]") == 2


class TestNavigateErrors:
    def test_missing_top_key_raises_missing(self) -> None:
        with pytest.raises(ConfigError) as exc_info:
            navigate({}, "absent")
        assert exc_info.value.reason is ConfigErrorReason.MISSING

    def test_missing_nested_key(self) -> None:
        with pytest.raises(ConfigError) as exc_info:
            navigate({"a": {"b": 1}}, "a.x")
        err = exc_info.value
        assert err.reason is ConfigErrorReason.MISSING
        assert err.path == "a.x"

    def test_array_index_out_of_range(self) -> None:
        with pytest.raises(ConfigError) as exc_info:
            navigate({"items": [1, 2, 3]}, "items[10]")
        err = exc_info.value
        assert err.reason is ConfigErrorReason.MISSING
        assert "[10]" in err.details

    def test_key_on_array_raises_type_mismatch(self) -> None:
        with pytest.raises(ConfigError) as exc_info:
            navigate({"items": [1, 2]}, "items.missing")
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH

    def test_index_on_dict_raises_type_mismatch(self) -> None:
        with pytest.raises(ConfigError) as exc_info:
            navigate({"a": {"b": 1}}, "a[0]")
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH

    def test_traverse_through_scalar_raises_type_mismatch(self) -> None:
        with pytest.raises(ConfigError) as exc_info:
            navigate({"a": 42}, "a.b")
        assert exc_info.value.reason is ConfigErrorReason.TYPE_MISMATCH

    def test_invalid_syntax_raises_missing(self) -> None:
        # Garbled path — reported as MISSING (can't navigate). Spec open question
        # whether to distinguish as PARSE_ERROR; currently MISSING for consistency.
        with pytest.raises(ConfigError) as exc_info:
            navigate({"a": 1}, "[[")
        assert exc_info.value.reason is ConfigErrorReason.MISSING

    def test_leading_index_error_format(self) -> None:
        # Path starting with [N] — the formatted error message must not have a leading dot.
        with pytest.raises(ConfigError) as exc_info:
            navigate([1, 2], "[5]")
        assert "[5]" in exc_info.value.path
