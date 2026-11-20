import pytest

from configstack import deep_merge, load_config, resolve


def test_nested_merge_and_list_replacement():
    layers = [{"a": {"x": 1, "y": 2}, "items": [1]}, {"a": {"y": 3}, "items": [2, 3]}]
    assert deep_merge(layers) == {"a": {"x": 1, "y": 3}, "items": [2, 3]}


def test_none_deletes_a_key():
    assert deep_merge([{"a": 1, "b": 2}, {"a": None}]) == {"b": 2}


def test_merge_does_not_mutate_or_alias_inputs():
    first = {"a": {"values": [1]}}
    merged = deep_merge([first, {"a": {"x": 2}}])
    merged["a"]["values"].append(9)
    assert first == {"a": {"values": [1]}}


def test_interpolation_default_for_missing_or_empty():
    assert resolve("${HOST:-localhost}", {}) == "localhost"
    assert resolve("${HOST:-localhost}", {"HOST": ""}) == "localhost"


def test_escaped_placeholder_is_literal():
    assert resolve("$${HOST}", {}) == "${HOST}"


def test_missing_required_variable_raises_key_error():
    with pytest.raises(KeyError):
        resolve("${HOST}", {})


def test_recursive_interpolation():
    value = {"a": ["${X}", 2], "b": True}
    assert resolve(value, {"X": "ok"}) == {"a": ["ok", 2], "b": True}


def test_load_normalizes_name_and_features():
    config = load_config([{"name": " api ", "port": 8080, "features": [" z ", "a", "a"]}], {})
    assert config == {"name": "api", "port": 8080, "features": ["a", "z"]}


def test_load_defaults_features_to_empty_list():
    assert load_config([{"name": "api", "port": 1}], {})["features"] == []


@pytest.mark.parametrize("port", [True, 0, 65536, "80"])
def test_load_rejects_invalid_port(port):
    with pytest.raises(ValueError):
        load_config([{"name": "api", "port": port}], {})
