import pytest

from funcs.make import make_processors


SPECS = [
    {"name": "a", "offset": 1, "limit": 2},
    {"name": "b", "offset": 10, "limit": 1},
]


def test_each_handler_captures_own_configuration():
    handlers = make_processors(SPECS)
    assert handlers["a"](5) == 6
    assert handlers["b"](5) == 15


def test_limits_are_per_handler():
    handlers = make_processors(SPECS)
    assert handlers["a"](0) == 1
    assert handlers["a"](0) == 1
    with pytest.raises(RuntimeError):
        handlers["a"](0)
    assert handlers["b"](0) == 10


def test_new_factory_has_fresh_state():
    first = make_processors([{"name": "x", "offset": 1, "limit": 1}])
    assert first["x"](0) == 1
    with pytest.raises(RuntimeError):
        first["x"](0)
    second = make_processors([{"name": "x", "offset": 2, "limit": 1}])
    assert second["x"](0) == 2


def test_factories_with_same_name_are_isolated():
    left = make_processors([{"name": "same", "offset": 1, "limit": 1}])
    right = make_processors([{"name": "same", "offset": 5, "limit": 1}])
    assert left["same"](0) == 1
    assert right["same"](0) == 5


def test_empty_specs():
    assert make_processors([]) == {}


def test_duplicate_names_are_rejected():
    with pytest.raises(ValueError):
        make_processors([
            {"name": "x", "offset": 1, "limit": 1},
            {"name": "x", "offset": 2, "limit": 1},
        ])


def test_non_positive_limit_is_rejected():
    with pytest.raises(ValueError):
        make_processors([{"name": "x", "offset": 1, "limit": 0}])


def test_input_specs_are_not_mutated():
    specs = [{"name": "x", "offset": 1, "limit": 1}]
    make_processors(specs)
    assert specs == [{"name": "x", "offset": 1, "limit": 1}]
