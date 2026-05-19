"""Unit tests for deep merge."""

from __future__ import annotations

from dagstack.config.merge import deep_merge, deep_merge_all


class TestDeepMergeBasic:
    def test_disjoint_keys_combined(self) -> None:
        result = deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_override_wins_for_scalar(self) -> None:
        result = deep_merge({"a": 1}, {"a": 2})
        assert result == {"a": 2}

    def test_base_preserved_when_override_empty(self) -> None:
        result = deep_merge({"a": 1, "b": 2}, {})
        assert result == {"a": 1, "b": 2}

    def test_override_preserved_when_base_empty(self) -> None:
        result = deep_merge({}, {"a": 1})
        assert result == {"a": 1}

    def test_both_empty(self) -> None:
        assert deep_merge({}, {}) == {}


class TestDeepMergeNested:
    def test_nested_dicts_merged_recursively(self) -> None:
        base = {"llm": {"model": "gpt-4", "temperature": 0.7}}
        override = {"llm": {"temperature": 0.2}}
        result = deep_merge(base, override)
        assert result == {"llm": {"model": "gpt-4", "temperature": 0.2}}

    def test_three_level_nesting(self) -> None:
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 10}}}
        assert deep_merge(base, override) == {"a": {"b": {"c": 10, "d": 2}}}

    def test_add_new_nested_key(self) -> None:
        base = {"llm": {"model": "gpt-4"}}
        override = {"llm": {"temperature": 0.5}}
        assert deep_merge(base, override) == {"llm": {"model": "gpt-4", "temperature": 0.5}}


class TestDeepMergeLists:
    def test_lists_replaced_atomically(self) -> None:
        # Spec §3: arrays are replaced atomically, NOT concatenated.
        base = {"plugins": ["a", "b", "c"]}
        override = {"plugins": ["x"]}
        assert deep_merge(base, override) == {"plugins": ["x"]}

    def test_list_not_merged_into_dict(self) -> None:
        base = {"x": {"a": 1}}
        override = {"x": [1, 2]}
        # Override is a list, base is a dict → override wins entirely.
        assert deep_merge(base, override) == {"x": [1, 2]}

    def test_dict_not_merged_into_list(self) -> None:
        base = {"x": [1, 2]}
        override = {"x": {"a": 1}}
        assert deep_merge(base, override) == {"x": {"a": 1}}


class TestDeepMergeImmutability:
    def test_base_not_mutated(self) -> None:
        base = {"a": {"b": 1}}
        override = {"a": {"b": 2}}
        deep_merge(base, override)
        assert base == {"a": {"b": 1}}

    def test_override_not_mutated(self) -> None:
        base = {"a": {"b": 1}}
        override = {"a": {"b": 2}}
        deep_merge(base, override)
        assert override == {"a": {"b": 2}}

    def test_nested_dict_not_shared_reference(self) -> None:
        base = {"a": {"b": 1}}
        override = {"c": 2}
        result = deep_merge(base, override)
        result["a"]["b"] = 999
        assert base["a"]["b"] == 1


class TestDeepMergeAll:
    def test_empty_list_returns_empty_dict(self) -> None:
        assert deep_merge_all([]) == {}

    def test_single_tree_returned_asis(self) -> None:
        tree = {"a": {"b": 1}}
        assert deep_merge_all([tree]) == {"a": {"b": 1}}

    def test_multiple_trees_in_priority_order(self) -> None:
        # Priority lowest → highest (the last tree overrides the rest).
        t1 = {"a": 1, "b": 2}
        t2 = {"b": 20, "c": 3}
        t3 = {"c": 30, "d": 4}
        result = deep_merge_all([t1, t2, t3])
        assert result == {"a": 1, "b": 20, "c": 30, "d": 4}

    def test_realistic_three_layer_config(self) -> None:
        # app-config.yaml → app-config.local.yaml → app-config.production.yaml
        base = {
            "llm": {"model": "gpt-4", "temperature": 0.7},
            "rag": {"top_k": 10},
        }
        local_override = {
            "llm": {"temperature": 0.2},  # dev lower temp
        }
        prod_override = {
            "llm": {"model": "gpt-4o"},
            "rag": {"top_k": 20},
        }
        result = deep_merge_all([base, local_override, prod_override])
        assert result == {
            "llm": {"model": "gpt-4o", "temperature": 0.2},
            "rag": {"top_k": 20},
        }
