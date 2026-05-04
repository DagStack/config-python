"""Unit tests for the Canonical JSON serializer (RFC 8785 subset per spec §9.1.1)."""

from __future__ import annotations

import pytest

from dagstack.config.canonical_json import canonical_json_dumpb, canonical_json_dumps


class TestPrimitives:
    def test_null(self) -> None:
        assert canonical_json_dumps(None) == "null"

    def test_true(self) -> None:
        assert canonical_json_dumps(True) == "true"

    def test_false(self) -> None:
        assert canonical_json_dumps(False) == "false"

    def test_empty_string(self) -> None:
        assert canonical_json_dumps("") == '""'

    def test_ascii_string(self) -> None:
        assert canonical_json_dumps("hello") == '"hello"'

    def test_unicode_string_not_escaped(self) -> None:
        # ensure_ascii=False: Unicode characters stay as UTF-8 in a Python str.
        # In bytes form — also UTF-8.
        assert canonical_json_dumps("привет") == '"привет"'
        assert canonical_json_dumpb("привет") == '"привет"'.encode()


class TestNumbers:
    def test_positive_integer(self) -> None:
        assert canonical_json_dumps(42) == "42"

    def test_negative_integer(self) -> None:
        assert canonical_json_dumps(-42) == "-42"

    def test_zero_int(self) -> None:
        assert canonical_json_dumps(0) == "0"

    def test_integer_without_decimal(self) -> None:
        # Int 1 → "1", NOT "1.0". Python's default json.dumps already does this.
        assert canonical_json_dumps(1) == "1"

    def test_whole_number_float_emits_integer_form(self) -> None:
        # v2.1 §9.1.1 / _meta/canonical_json.yaml: whole-number floats
        # in the i-JSON safe range are emitted in integer form, parity
        # with Go FormatFloat('g'). v0.1.0 produced "1.0"; v0.2.0
        # produces "1".
        assert canonical_json_dumps(1.0) == "1"
        assert canonical_json_dumps(100.0) == "100"
        assert canonical_json_dumps(-42.0) == "-42"

    def test_fractional_float_preserved(self) -> None:
        # Float with a fractional part → shortest round-trip (Python __repr__).
        assert canonical_json_dumps(0.1) == "0.1"
        assert canonical_json_dumps(0.75) == "0.75"
        assert canonical_json_dumps(0.30000000000000004) == "0.30000000000000004"

    def test_negative_zero_normalized(self) -> None:
        # RFC 8785 §3.2.2.3 + v2.1: -0.0 → "0" (integer form, since
        # -0.0.is_integer() == True and value == 0).
        assert canonical_json_dumps(-0.0) == "0"
        assert canonical_json_dumps(0.0) == "0"

    def test_out_of_range_whole_number_float_preserved(self) -> None:
        # Whole-number float outside the i-JSON safe range (±(2^53-1))
        # stays as float (precision not guaranteed).
        huge = 2.0**60
        assert canonical_json_dumps(huge) == repr(huge)

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValueError, match="NaN"):
            canonical_json_dumps(float("nan"))

    def test_positive_infinity_rejected(self) -> None:
        with pytest.raises(ValueError, match="Infinity"):
            canonical_json_dumps(float("inf"))

    def test_negative_infinity_rejected(self) -> None:
        with pytest.raises(ValueError, match="Infinity"):
            canonical_json_dumps(float("-inf"))


class TestContainers:
    def test_empty_array(self) -> None:
        assert canonical_json_dumps([]) == "[]"

    def test_empty_object(self) -> None:
        assert canonical_json_dumps({}) == "{}"

    def test_array_of_ints(self) -> None:
        assert canonical_json_dumps([1, 2, 3]) == "[1,2,3]"

    def test_array_preserves_order(self) -> None:
        # Array element order is sensitive (unlike dict keys).
        assert canonical_json_dumps([3, 1, 2]) == "[3,1,2]"

    def test_object_keys_sorted(self) -> None:
        # Dict keys are sorted lexicographically (UTF-8 code points).
        assert canonical_json_dumps({"b": 2, "a": 1}) == '{"a":1,"b":2}'

    def test_nested_object_recursively_sorted(self) -> None:
        obj = {"outer": {"z": 1, "a": 2}, "other": 3}
        assert canonical_json_dumps(obj) == '{"other":3,"outer":{"a":2,"z":1}}'

    def test_mixed_nested(self) -> None:
        obj = {
            "plugins": ["tool_a", "tool_b"],
            "config": {"database": {"name": "primary", "pool_size": 20}},
        }
        assert (
            canonical_json_dumps(obj)
            == '{"config":{"database":{"name":"primary","pool_size":20}},"plugins":["tool_a","tool_b"]}'
        )


class TestSeparators:
    def test_no_whitespace_anywhere(self) -> None:
        result = canonical_json_dumps({"a": [1, 2, {"b": "c"}]})
        assert " " not in result
        assert "\t" not in result
        assert "\n" not in result

    def test_no_trailing_newline(self) -> None:
        result = canonical_json_dumps({"x": 1})
        assert not result.endswith("\n")


class TestValidation:
    def test_non_string_dict_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-string"):
            canonical_json_dumps({1: "value"})

    def test_nested_non_string_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-string"):
            canonical_json_dumps({"ok": {1: "bad"}})


class TestUnicodeSortOrder:
    def test_ascii_keys_sorted(self) -> None:
        assert canonical_json_dumps({"c": 1, "a": 2, "b": 3}) == '{"a":2,"b":3,"c":1}'

    def test_unicode_keys_sorted_by_code_point(self) -> None:
        # Python's sorted() works by Unicode code point — matches RFC 8785.
        result = canonical_json_dumps({"я": 1, "а": 2, "б": 3})
        assert result == '{"а":2,"б":3,"я":1}'

    def test_mixed_ascii_unicode_keys(self) -> None:
        # ASCII code points < Cyrillic code points.
        result = canonical_json_dumps({"a": 1, "я": 2, "b": 3})
        assert result == '{"a":1,"b":3,"я":2}'


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        obj = {"b": [1, 2], "a": {"nested": True, "val": 3.14}}
        assert canonical_json_dumps(obj) == canonical_json_dumps(obj)

    def test_different_input_order_same_output(self) -> None:
        # Keys in any order → the same canonical output.
        a = {"x": 1, "y": 2, "z": 3}
        b = {"z": 3, "y": 2, "x": 1}
        assert canonical_json_dumps(a) == canonical_json_dumps(b)

    def test_bytes_output_is_utf8_encoded_str(self) -> None:
        obj = {"message": "привет"}
        assert canonical_json_dumpb(obj) == canonical_json_dumps(obj).encode("utf-8")
