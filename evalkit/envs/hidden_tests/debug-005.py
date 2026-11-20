"""Hidden checks for snapshot debug-005.

The visible suite pins one happy factory, two handlers with *different* limits, one
duplicate-name spec and one non-positive limit. Everything it can see, a shallow
repair can satisfy; the promises that stay live are the ones it never drives:

* "每个 handler 要捕获自己的配置" -- binding the loop's ``spec`` **dict** as a default
  argument looks exactly like the textbook late-binding fix and passes every visible
  test, but the handler still reads through to the caller's mutable config;
* "限额按 handler" -- a counter keyed by ``limit`` instead of by handler lets two
  handlers with the same limit eat each other's budget (the shipped specs use limits
  1 and 2, i.e. never equal, so the visible suite cannot see it);
* "新工厂必须是新状态" -- a factory memoised on the specs object hands the same
  handlers (and the same budget) back on the next call; the visible suite always
  builds a fresh literal list, so ``id(...)``-keyed state is invisible there;
* the quota is a quota: once a handler has exceeded ``limit`` it stays exceeded. A
  counter that clears the name when it trips keeps every visible test green.

Every expectation was MEASURED (pytest in ``agent-eval-pytest:3.12``) on the 34
repaired workspaces replayed out of ``runs/*/*/debug-stacktrace-005``: they are
unanimous on all of it (only the two do-nothing attempts differ, and those fail the
visible suite too). Deliberately NOT asserted: the shape of ``funcs/state.py`` (the
passing population rewrote it 8 different ways -- class, closure factory, dict
passed in, ...), the exception type for a non-numeric ``limit`` (``None`` splits
TypeError vs ValueError across the population), and error message text.
"""
import copy

import pytest

from funcs.make import make_processors


def spec(name, offset, limit):
    return {"name": name, "offset": offset, "limit": limit}


def outcome(handler, value):
    """Return what the call produced, or the exception's class name if it raised."""
    try:
        return handler(value)
    except Exception as exc:
        return type(exc).__name__


def ladder(calls):
    return [outcome(handler, value) for handler, value in calls]


def run(handlers, plan):
    """plan is a list of (name, value); results in order."""
    return ladder([(handlers[name], value) for name, value in plan])


# --------------------------------------------------------------------------
# each handler captures its OWN configuration (not a live window onto the caller's)
# --------------------------------------------------------------------------
def test_handler_does_not_read_the_callers_spec_back_again():
    # measured [1, 1]: the textbook-looking `def process(value, spec=spec)` passes the
    # visible suite but returns 500 here -- the handler borrowed the caller's dict
    # instead of capturing its configuration.
    specs = [spec("y", 1, 9)]
    handlers = make_processors(specs)
    specs[0]["offset"] = 500
    specs[0]["limit"] = 0
    assert run(handlers, [("y", 0), ("y", 0)]) == [1, 1]


def test_handler_does_not_read_the_spec_name_back_either():
    # measured [1, 1, 'RuntimeError']: renaming the entry after construction must not
    # move this handler's budget or its identity.
    specs = [spec("y", 1, 2)]
    handlers = make_processors(specs)
    specs[0]["name"] = "other"
    assert run(handlers, [("y", 0), ("y", 0), ("y", 0)]) == [1, 1, "RuntimeError"]


def test_each_handler_adds_its_own_offset_to_the_value_it_is_given():
    handlers = make_processors([spec("a", 1, 9), spec("b", 10, 9)])
    assert sorted(handlers) == ["a", "b"]
    assert all(callable(handler) for handler in handlers.values())
    # measured 8, 17, -1, 8
    assert run(handlers, [("a", 7), ("b", 7), ("a", -2), ("b", -2)]) == [8, 17, -1, 8]


def test_empty_specs_still_return_an_empty_mapping():
    assert make_processors([]) == {}


# --------------------------------------------------------------------------
# the quota is per handler: not per limit value, not per factory, not per call
# --------------------------------------------------------------------------
def test_two_handlers_that_share_a_limit_value_have_separate_budgets():
    # The shipped specs use limits 1 and 2, so a counter keyed by `limit` instead of by
    # handler is invisible to the visible suite. Measured
    # [1, 1, 'RuntimeError', 10, 10, 'RuntimeError'].
    handlers = make_processors([spec("a", 1, 2), spec("b", 10, 2)])
    assert run(handlers, [("a", 0), ("a", 0), ("a", 0),
                          ("b", 0), ("b", 0), ("b", 0)]) == [
        1, 1, "RuntimeError", 10, 10, "RuntimeError"]


def test_interleaved_handlers_do_not_borrow_from_each_other():
    # measured [1, 10, 2, 11, 3, 'RuntimeError', 'RuntimeError', 'RuntimeError']
    handlers = make_processors([spec("a", 1, 3), spec("b", 10, 2)])
    assert run(handlers, [("a", 0), ("b", 0), ("a", 1), ("b", 1),
                          ("a", 2), ("b", 2), ("a", 3), ("a", 4)]) == [
        1, 10, 2, 11, 3, "RuntimeError", "RuntimeError", "RuntimeError"]


@pytest.mark.parametrize("limit", [1, 2, 3, 5])
def test_the_budget_is_exactly_limit_calls(limit):
    # measured: calls 0..limit-1 succeed and call number limit+1 raises. The fixture
    # only ever uses limits 1 and 2, so an off-by-one survives the visible suite.
    handlers = make_processors([spec("x", 0, limit)])
    assert run(handlers, [("x", i) for i in range(limit + 1)]) == [
        *range(limit), "RuntimeError"]


def test_construction_consumes_no_budget():
    # measured [1, 2, 3, 'RuntimeError', 'RuntimeError', 'RuntimeError']
    handlers = make_processors([spec("a", 1, 1), spec("b", 2, 1), spec("c", 3, 1)])
    assert run(handlers, [("a", 0), ("b", 0), ("c", 0),
                          ("a", 0), ("b", 0), ("c", 0)]) == [
        1, 2, 3, "RuntimeError", "RuntimeError", "RuntimeError"]


# --------------------------------------------------------------------------
# exceeding a limit is terminal, not a window that reopens
# --------------------------------------------------------------------------
def test_an_exhausted_handler_stays_exhausted():
    # measured [5, 'RuntimeError', 'RuntimeError', 'RuntimeError']: a counter that
    # clears the name when it trips -- so the handler "recovers" on the next call --
    # keeps all eight visible tests green.
    handlers = make_processors([spec("x", 5, 1)])
    assert run(handlers, [("x", 0), ("x", 0), ("x", 0), ("x", 0)]) == [
        5, "RuntimeError", "RuntimeError", "RuntimeError"]


def test_exhausting_one_factory_does_not_reopen_another():
    # measured [1, 1, 'RuntimeError'] / [100] / ['RuntimeError'] /
    # [100, 'RuntimeError', 'RuntimeError']
    first = make_processors([spec("q", 1, 2)])
    second = make_processors([spec("q", 100, 2)])
    assert run(first, [("q", 0), ("q", 0), ("q", 0)]) == [1, 1, "RuntimeError"]
    assert run(second, [("q", 0)]) == [100]
    assert run(first, [("q", 0)]) == ["RuntimeError"]
    assert run(second, [("q", 0), ("q", 0), ("q", 0)]) == [
        100, "RuntimeError", "RuntimeError"]


# --------------------------------------------------------------------------
# a new factory is new state, whatever object the specs came in
# --------------------------------------------------------------------------
def test_the_same_specs_list_twice_is_still_two_factories():
    # measured [1, 1, 'RuntimeError'] each. The visible suite builds every factory
    # from a fresh literal list, so a factory memoised on the specs object
    # (id(specs) -> handlers) passes it and shares one budget here.
    specs = [{"name": "s", "offset": 1, "limit": 2}]
    first = make_processors(specs)
    second = make_processors(specs)
    assert first is not second
    assert run(first, [("s", 0), ("s", 0), ("s", 0)]) == [1, 1, "RuntimeError"]
    assert run(second, [("s", 0), ("s", 0), ("s", 0)]) == [1, 1, "RuntimeError"]
    assert run(first, [("s", 0), ("s", 0)]) == ["RuntimeError", "RuntimeError"]


def test_two_factories_interleave_without_interference():
    # measured [1, 1, 1, 1, 'RuntimeError', 'RuntimeError']: calls from two factories
    # that share a handler name are interleaved, so a module-level counter keyed only
    # by name (the original defect) trips the second factory early.
    shared = [{"name": "q", "offset": 1, "limit": 2}]
    first = make_processors(shared)
    second = make_processors(shared)
    assert ladder([(first["q"], 0), (second["q"], 0), (first["q"], 0),
                   (second["q"], 0), (first["q"], 0), (second["q"], 0)]) == [
        1, 1, 1, 1, "RuntimeError", "RuntimeError"]
    assert ladder([(second["q"], 0), (first["q"], 0)]) == [
        "RuntimeError", "RuntimeError"]


def test_a_rejected_construction_leaves_no_state_behind():
    # measured [7, 'RuntimeError']: two constructions that must both be refused, then a
    # clean factory whose handler still gets its whole budget.
    for bad in ([spec("w", 1, 0)],
                [spec("w", 1, 1), spec("w", 2, 1)]):
        before = copy.deepcopy(bad)
        with pytest.raises(ValueError):
            make_processors(bad)
        assert bad == before, "make_processors rewrote the specs it refused"
    handlers = make_processors([spec("w", 7, 1)])
    assert run(handlers, [("w", 0), ("w", 0)]) == [7, "RuntimeError"]


# --------------------------------------------------------------------------
# both construction-time guards scan the whole list
# --------------------------------------------------------------------------
def test_a_bad_limit_anywhere_in_the_list_is_rejected():
    # measured ValueError. A guard that only looks at specs[0] hands back a factory.
    with pytest.raises(ValueError):
        make_processors([spec("p", 1, 2), spec("q", 1, 0), spec("r", 1, 2)])
    with pytest.raises(ValueError):
        make_processors([spec("p", 1, 3), spec("q", 2, -4)])
    with pytest.raises(ValueError):
        make_processors([spec("p", 1, 1), spec("q", 2, 1), spec("r", 3, 0)])


def test_duplicate_names_are_rejected_wherever_they_sit():
    # measured ValueError, including the identical-spec pair that a
    # "same configuration twice is not a conflict" dedup lets through.
    with pytest.raises(ValueError):
        make_processors([spec("p", 1, 2), spec("q", 2, 2), spec("p", 3, 2)])
    with pytest.raises(ValueError):
        make_processors([spec("z", 1, 1), spec("z", 1, 1)])
    with pytest.raises(ValueError):
        make_processors([spec("z", 1, 1)] * 3)



