"""Hidden checks for snapshot fix-005.

The visible suite pins nine hand-picked shapes: a leaf override, one nested merge,
one list replacement, one None replacement, one two-level aliasing probe, and two
resolve precedence checks. Every one of those shapes is *shallow* (two levels) and
every one passes an operand that is non-empty on both sides. That leaves the two
halves of the promised contract wide open:

  * "dict 递归合并" — a merge that recurses only as deep as the visible tests
    (two levels), that merges lists element-wise instead of replacing them, or that
    treats an empty mapping override as "no override", is invisible to the suite;
  * "结果不能与输入共享可变子对象" — the aliasing probe mutates a list that came
    from `base` through a *merged* key, so an implementation that isolates `base`
    and hands `override`'s values through by reference, or that short-circuits an
    empty operand by returning it unchanged (result IS the caller's dict), stays
    green while still sharing mutable state with its inputs;
  * falsy-but-not-None overrides (0, "", [], False) — `test_none_is_an_explicit_
    override` pins None only, so `if value:` / `if value is not None and value:`
    survives the visible suite;
  * `resolve`'s three layers are only ever exercised two levels deep with all three
    layers non-empty and each key present in at least two layers, so a hand rolled
    resolve that passes those two shapes can still cap its precedence chain at one
    level, and can return objects it shares with the caller's config layers.

Expected values come from `reference_merge` / `reference_resolve` below, which
implement the stated contract independently of the repo; the literals are measured
against them (and against the reference artifact), never derived by hand. Only
`dict` and `list` are treated as "mutable sub-objects", the two types the prompt
names — no case here depends on how a set or a tuple is copied.
"""
from copy import deepcopy

import pytest

from cfg.merge import merge
from cfg.resolve import resolve

MUTABLE = (dict, list)
_ABSENT = object()


def reference_merge(base, override):
    """题面口径：dict 递归合并，其他类型由 override 整体替换，且不做变异。"""
    result = {}
    for key, value in base.items():
        if key not in override:
            result[key] = deepcopy(value)
    for key, value in override.items():
        below = base[key] if key in base else _ABSENT
        if isinstance(below, dict) and isinstance(value, dict):
            result[key] = reference_merge(below, value)
        else:
            result[key] = deepcopy(value)
    return result


def reference_resolve(defaults, file_config, env_config):
    return reference_merge(reference_merge(defaults, file_config), env_config)


def containers(obj, depth=0, out=None):
    """Every dict/list reachable from obj (through dict/list edges only)."""
    if out is None:
        out = set()
    if depth > 12 or isinstance(obj, MUTABLE) and id(obj) in out:
        return out
    if isinstance(obj, MUTABLE):
        out.add(id(obj))
        for value in (obj.values() if isinstance(obj, dict) else obj):
            containers(value, depth + 1, out)
    return out


def shared_object(result, *inputs):
    """(label, id) of the first mutable object the result shares with an input."""
    mine = containers(result)
    for label, value in zip(("base", "override", "defaults", "file", "env"), inputs):
        hit = mine & containers(value)
        if hit:
            return f"shares a mutable sub-object with the {label} layer (id {sorted(hit)[0]})"
    return None


def fresh(*objects):
    return [deepcopy(o) for o in objects]


# 每一行：一个可见套件从没构造过的形状（深度、空侧、falsy、list 套 dict）。
MERGE_SHAPES = {
    "depth_three_recursion": ({"a": {"b": {"c": 1, "keep": 0}}}, {"a": {"b": {"d": 2}}}),
    "depth_four_both_sides": (
        {"srv": {"tls": {"ca": {"path": "/x", "pw": "p"}}}},
        {"srv": {"tls": {"ca": {"path": "/y"}}}},
    ),
    "deep_sibling_survives": (
        {"a": {"b": {"c": {"deep": [1, 2]}}}, "keep": {"untouched": True}},
        {"a": {"b": {"other": "v"}}},
    ),
    "empty_mapping_override": ({"a": {"x": 1}}, {"a": {}}),
    "empty_mapping_base": ({"a": {}}, {"a": {"x": 1}}),
    "empty_dict_at_depth": ({"a": {"b": {"x": 1}}}, {"a": {"b": {}}}),
    "falsy_scalars": ({"n": 7, "s": "keep", "b": True}, {"n": 0, "s": "", "b": False}),
    "falsy_containers": ({"l": [1, 2], "m": {"k": 1}}, {"l": [], "m": {}}),
    "falsy_nested": ({"a": {"n": 5, "l": [1]}}, {"a": {"n": 0, "l": []}}),
    "list_of_mappings_replaced": (
        {"a": {"steps": [{"id": 1}, {"id": 2}]}},
        {"a": {"steps": [{"id": 9}]}},
    ),
    "list_of_equal_length_mappings": (
        {"a": {"rows": [{"x": 1}, {"y": 2}]}},
        {"a": {"rows": [{"x": 5}, {"y": 6}]}},
    ),
    # 逐元素合并 list 的实现（deepmerge 的 recursive 策略）在这三条上和"整体替换"
    # 分道扬镳：base 的 list 元素带着 override 没有的键。
    "list_items_keep_no_base_keys": (
        {"a": {"rows": [{"x": 1, "only_base": True}]}},
        {"a": {"rows": [{"x": 5}]}},
    ),
    "shorter_list_drops_trailing_base_items": (
        {"a": {"rows": [{"x": 1}, {"y": 2}]}},
        {"a": {"rows": [{"x": 5}]}},
    ),
    "nested_scalar_list_replaced": ({"a": {"l": [1, 2, 3]}}, {"a": {"l": [9]}}),
    # 空容器也是 override：只有"override 是 dict 且递归"这一条能短路，
    # base 侧不是 dict 时必须整体替换。
    "empty_dict_replaces_a_scalar": ({"a": {"x": 1}, "b": 5}, {"b": {}}),
    "empty_dict_replaces_a_list": ({"l": [1, 2]}, {"l": {}}),
    "empty_list_replaces_a_mapping": ({"a": {"x": 1}}, {"a": []}),
    "none_at_depth": ({"a": {"b": {"c": 1}, "d": 2}}, {"a": {"b": None}}),
    "mapping_replaces_scalar_deep": ({"a": {"b": "auto"}}, {"a": {"b": {"on": True}}}),
    "scalar_replaces_mapping_deep": ({"a": {"b": {"on": True}}}, {"a": {"b": 3}}),
    "empty_override": ({"a": {"x": 1}, "lst": [1, 2]}, {}),
    "empty_base": ({}, {"a": {"x": 1}, "lst": [1, 2]}),
    "both_empty": ({}, {}),
    "disjoint_deep_branches": ({"p": {"q": [1]}}, {"r": {"s": {"t": [2]}}}),
    "unicode_and_tuple_keys": ({"超时": {"值": (1, 2)}}, {"超时": {"另一": 3}}),
}


@pytest.mark.parametrize("shape", sorted(MERGE_SHAPES))
def test_merge_result_matches_the_stated_contract_shape(shape):
    base, override = MERGE_SHAPES[shape]
    expected = reference_merge(base, override)
    assert merge(*fresh(base, override)) == expected


@pytest.mark.parametrize("shape", sorted(MERGE_SHAPES))
def test_result_shares_no_mutable_subobject_with_the_inputs(shape):
    base, override = MERGE_SHAPES[shape]
    result = merge(*fresh(base, override))
    problem = shared_object(result, base, override)
    assert problem is None, problem


@pytest.mark.parametrize("shape", sorted(MERGE_SHAPES))
def test_merge_returns_a_fresh_top_level_dict(shape):
    base, override = MERGE_SHAPES[shape]
    result = merge(base, override)
    assert isinstance(result, dict)
    assert result is not base, "merge returned the base object itself"
    assert result is not override, "merge returned the override object itself"


@pytest.mark.parametrize("shape", sorted(MERGE_SHAPES))
def test_inputs_are_unchanged_and_survive_writing_through_the_result(shape):
    base, override = MERGE_SHAPES[shape]
    probe = {"__probe__": [0]}
    base_copy, override_copy = fresh(base, override)
    result = merge(base, override)
    assert (base, override) == (base_copy, override_copy), "call mutated an input"
    # 顺着结果写下去，不得回写到任何调用方还持有的对象上
    stack = [(result, 0)]
    seen = set()
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, MUTABLE) or id(node) in seen or depth > 10:
            continue
        seen.add(id(node))
        if isinstance(node, dict):
            node["__probe__"] = probe
            for value in node.values():
                stack.append((value, depth + 1))
        else:
            node.append(probe)
    assert (base, override) == (base_copy, override_copy), \
        f"{shape}: writing through the result leaked into the caller's dicts/lists"


def test_a_nested_merged_value_is_not_one_of_the_input_subtrees():
    base = {"db": {"host": "h", "opts": {"pool": [1]}}}
    override = {"db": {"port": 5432}}
    result = merge(base, override)
    assert result["db"] is not base["db"]
    assert result["db"] is not override["db"]
    assert result["db"]["opts"] is not base["db"]["opts"]
    assert result["db"]["opts"]["pool"] is not base["db"]["opts"]["pool"]
    result["db"]["opts"]["pool"].append(2)
    result["db"]["port"] = 1
    assert base == {"db": {"host": "h", "opts": {"pool": [1]}}}, "base was mutated"
    assert override == {"db": {"port": 5432}}, "override was mutated"


def test_repeated_calls_do_not_leak_state_between_them():
    base = {"a": {"x": [1]}, "b": 2}
    override = {"a": {"y": 3}}
    first = merge(base, override)
    second = merge(base, override)
    assert first == second
    assert first is not second
    assert first["a"] is not second["a"]
    assert first["a"]["x"] is not second["a"]["x"]
    first["a"]["x"].append(99)
    assert second == {"a": {"x": [1], "y": 3}, "b": 2}


RESOLVE_SHAPES = {
    "three_layer_deep_chain": (
        {"a": {"b": {"c": 0, "only_defaults": 1}}},
        {"a": {"b": {"f": 1, "c": 1}}},
        {"a": {"b": {"c": 2}}},
    ),
    "each_layer_adds_its_own_branch": (
        {"db": {"host": "h"}, "log": {"level": "warn"}},
        {"db": {"port": 1}, "log": {"fmt": "json"}},
        {"db": {"ssl": True}},
    ),
    "env_clears_with_none": ({"token": "d", "keep": 1}, {"token": "f"}, {"token": None}),
    "env_clears_with_falsy": ({"retries": 5, "tags": ["a"]}, {"retries": 2}, {"retries": 0}),
    "file_list_wins_over_defaults": ({"p": [1, 2]}, {"p": [3]}, {}),
    "env_empty_list_replaces": ({"p": [1, 2]}, {"p": [3]}, {"p": []}),
    "file_layer_is_untouched_by_env": ({"a": 1}, {"b": {"c": [1]}}, {"d": "e"}),
    "scalar_and_mapping_swap_across_layers": ({"f": {"on": True}}, {"f": {"extra": 1}}, {"f": "auto"}),
    "mapping_wins_last": ({"f": "auto"}, {"f": {"on": False}}, {"f": {"on": True}}),
    "empty_env_layer": ({"a": {"x": 1}}, {"b": 2}, {}),
    "empty_file_layer": ({"a": {"x": 1}}, {}, {"a": {"y": 2}}),
    "empty_defaults_layer": ({}, {"a": {"x": 1}}, {"a": {"y": 2}}),
}


@pytest.mark.parametrize("shape", sorted(RESOLVE_SHAPES))
def test_resolve_precedence_matches_the_stated_contract_shape(shape):
    layers = RESOLVE_SHAPES[shape]
    expected = reference_resolve(*layers)
    assert resolve(*fresh(*layers)) == expected


@pytest.mark.parametrize("shape", sorted(RESOLVE_SHAPES))
def test_resolve_shares_no_mutable_subobject_with_any_layer(shape):
    defaults, file_config, env_config = RESOLVE_SHAPES[shape]
    before = (deepcopy(defaults), deepcopy(file_config), deepcopy(env_config))
    result = resolve(defaults, file_config, env_config)
    problem = shared_object(result, defaults, file_config, env_config)
    assert problem is None, problem
    assert (defaults, file_config, env_config) == before, "resolve mutated a config layer"


def test_resolve_isolates_the_result_from_the_env_layer_it_replaced_with():
    defaults = {"db": {"host": "h", "opts": {"pool": [1]}}}
    file_config = {"db": {"port": 5432}}
    env_config = {"db": {"ssl": True}, "plugins": ["a"]}
    result = resolve(defaults, file_config, env_config)
    assert result == {
        "db": {"host": "h", "opts": {"pool": [1]}, "port": 5432, "ssl": True},
        "plugins": ["a"],
    }
    assert result["db"] is not defaults["db"] and result["db"] is not env_config["db"]
    assert result["db"]["opts"] is not defaults["db"]["opts"]
    assert result["plugins"] is not env_config["plugins"]
    result["plugins"].append("b")
    result["db"]["opts"]["pool"].append(2)
    assert env_config == {"db": {"ssl": True}, "plugins": ["a"]}
    assert defaults == {"db": {"host": "h", "opts": {"pool": [1]}}}


def test_resolve_chains_through_merge_for_an_unusual_leaf_type():
    # 三层里最不像测试用例的一层：env 用空 list 关掉 defaults 的功能开关，
    # file 在第四层加一个键；优先级与递归必须同时成立。
    defaults = {"app": {"features": {"beta": [1, 2, 3]}, "meta": {"owner": {"name": "x"}}}}
    file_config = {"app": {"meta": {"owner": {"team": "y"}}}}
    env_config = {"app": {"features": {"beta": []}}}
    result = resolve(defaults, file_config, env_config)
    assert result == {
        "app": {"features": {"beta": []}, "meta": {"owner": {"name": "x", "team": "y"}}}
    }
    assert result["app"]["meta"]["owner"] is not file_config["app"]["meta"]["owner"]
