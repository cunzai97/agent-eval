from copy import deepcopy

from cfg.merge import merge
from cfg.resolve import resolve


def test_override_wins_at_leaf():
    assert merge({"a": 1}, {"a": 9}) == {"a": 9}


def test_nested_dicts_merge_recursively():
    assert merge({"db": {"host": "h", "port": 1}}, {"db": {"port": 2}}) == {
        "db": {"host": "h", "port": 2}
    }


def test_lists_replace_instead_of_concatenate():
    assert merge({"plugins": ["a", "b"]}, {"plugins": ["c"]}) == {"plugins": ["c"]}


def test_none_is_an_explicit_override():
    assert merge({"token": "secret"}, {"token": None}) == {"token": None}


def test_inputs_are_not_mutated_or_aliased():
    base = {"nested": {"values": [1, 2]}}
    override = {"nested": {"flag": True}}
    before = (deepcopy(base), deepcopy(override))
    result = merge(base, override)
    result["nested"]["values"].append(3)
    assert (base, override) == before


def test_scalar_can_replace_mapping():
    assert merge({"feature": {"on": True}}, {"feature": "auto"}) == {"feature": "auto"}


def test_mapping_can_replace_scalar():
    assert merge({"feature": "auto"}, {"feature": {"on": True}}) == {"feature": {"on": True}}


def test_resolve_precedence_is_defaults_file_env():
    result = resolve({"a": 0, "b": 0}, {"a": 1}, {"a": 2, "b": 3})
    assert result == {"a": 2, "b": 3}


def test_resolve_keeps_nested_values_from_all_layers():
    result = resolve({"db": {"host": "h", "port": 1}}, {"db": {"port": 2}}, {"db": {"ssl": True}})
    assert result == {"db": {"host": "h", "port": 2, "ssl": True}}
