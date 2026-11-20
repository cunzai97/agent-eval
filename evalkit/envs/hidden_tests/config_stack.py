from collections import UserDict

import pytest

from configstack import deep_merge, load_config, resolve


def test_mapping_layers_and_string_keys():
    assert deep_merge([UserDict({"a": 1})]) == {"a": 1}


@pytest.mark.parametrize("layer", [None, [], "x"])
def test_non_mapping_layer_rejected(layer):
    with pytest.raises(TypeError):
        deep_merge([layer])


def test_non_string_key_rejected():
    with pytest.raises(TypeError):
        deep_merge([{1: "x"}])


def test_nested_none_deletes_only_target():
    assert deep_merge([{"a": {"x": 1, "y": 2}}, {"a": {"x": None}}]) == {"a": {"y": 2}}


def test_delete_missing_key_is_noop():
    assert deep_merge([{"a": 1}, {"missing": None}]) == {"a": 1}


def test_none_in_first_layer_is_absent():
    assert deep_merge([{"a": None}, {"b": 2}]) == {"b": 2}


def test_scalar_replaces_mapping_and_reverse():
    assert deep_merge([{"a": {"x": 1}}, {"a": 2}, {"a": {"y": 3}}]) == {"a": {"y": 3}}


def test_lists_are_replaced_and_deep_copied():
    source = {"a": [{"x": 1}]}
    merged = deep_merge([source])
    merged["a"][0]["x"] = 9
    assert source["a"][0]["x"] == 1


def test_merge_does_not_mutate_later_layer():
    later = {"a": {"x": 1}}
    merged = deep_merge([{"a": {"y": 2}}, later])
    merged["a"]["x"] = 8
    assert later == {"a": {"x": 1}}


def test_embedded_and_multiple_placeholders():
    assert resolve("http://${HOST}:${PORT}/x", {"HOST": "local", "PORT": 80}) == "http://local:80/x"


def test_required_empty_variable_is_allowed():
    assert resolve("${X}", {"X": ""}) == ""


def test_default_may_be_empty():
    assert resolve("${X:-}", {}) == ""
    assert resolve("${X:-}", {"X": "value"}) == "value"


def test_escaped_default_placeholder_is_literal():
    assert resolve("$${X:-fallback}", {}) == "${X:-fallback}"


def test_escaped_placeholder_does_not_read_env():
    assert resolve("$${X}", {"X": "expanded"}) == "${X}"


@pytest.mark.parametrize("text", ["${}", "${1X}", "${X", "plain"])
def test_malformed_or_plain_strings_stay_literal(text):
    assert resolve(text, {}) == text


def test_unclosed_escape_stays_literal():
    assert resolve("$${X", {}) == "$${X"


def test_dict_keys_are_not_interpolated():
    assert resolve({"${X}": "${X}"}, {"X": "v"}) == {"${X}": "v"}


def test_non_container_values_preserved():
    assert resolve((1, 2), {}) == (1, 2)
    assert resolve(None, {}) is None


def test_load_preserves_unknown_keys():
    config = load_config([{"name": "api", "port": 80, "custom": {"x": 1}}], {})
    assert config["custom"] == {"x": 1}


@pytest.mark.parametrize("features", ["a", [1], ["a", "  "]])
def test_invalid_features_rejected(features):
    with pytest.raises(ValueError):
        load_config([{"name": "api", "port": 80, "features": features}], {})


@pytest.mark.parametrize("name", ["", "  ", None, 3])
def test_invalid_name_rejected(name):
    with pytest.raises(ValueError):
        load_config([{"name": name, "port": 80}], {})


@pytest.mark.parametrize("port", [-1, 1.5, False, None])
def test_more_invalid_ports_rejected(port):
    with pytest.raises(ValueError):
        load_config([{"name": "api", "port": port}], {})


def test_full_pipeline_delete_interpolate_and_normalize():
    layers = [
        {"name": "old", "port": 1, "features": ["old"], "remove": True},
        {"name": " ${NAME} ", "port": 8080, "features": [" b ", "a", "b"], "remove": None},
    ]
    assert load_config(layers, {"NAME": "api"}) == {
        "name": "api", "port": 8080, "features": ["a", "b"],
    }
