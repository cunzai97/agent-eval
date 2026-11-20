"""Hidden checks for snapshot debug-002.

The visible suite is a smoke suite over ONE fixture, ``RAGGED = [[1, 2, 3], [4], [],
[5, 6]]``, and that fixture is badly chosen: its longest row is its *first* row, and
its height (4) / width (3) are the only bounds it ever exercises. Every assertion it
makes is therefore also satisfied by a repair that anchors the bounds on
``len(grid[0])`` or on a literal 3/4, or by a ``transpose`` that has forgotten what
``fill`` is for. Each such shortcut leaves the defect live and is invisible from
``tests/test_grid.py``.

Expectations below were MEASURED (pytest in ``agent-eval-pytest:3.12``) on the 37
repaired workspaces replayed out of ``runs/*/*/debug-stacktrace-002`` -- they are
unanimous on all of them -- and on the shortcut families:

* anchor-on-first-row  -> killed by ``test_transpose_uses_the_longest_row...`` and
  ``test_col_sum_is_not_anchored_on_the_first_row``;
* literal 3/4 bounds     -> killed by ``test_col_sum_bound_follows_the_grid...`` and
  ``test_row_sum_bound_follows_the_grid...``;
* ``fill`` ignored / default moved -> ``test_transpose_pads_with_the_fill_argument``,
  ``test_transpose_default_fill_is_zero``;
* padding the caller's rows in place, so the caller's data changes under it -- in the
  visible suite ``test_transpose_*`` is the last test that reads ``RAGGED``, so the
  corruption happens after the last assertion. The task prompt states this contract
  explicitly ("缺失单元只在读到的结果里算作 0 ... 不得改写调用方传入的 grid");
  -> killed by the three ``does_not_mutate`` / freshness cases;
* ``max(len(row) for row in grid)`` as the bound, and a negative-index guard behind an
  ``if not grid: return 0`` early exit -> ``test_empty_grid_is_a_zero_sum_not_a_crash``;
* special-casing the shipped numbers -> the parametrised reference battery at the bottom.
"""
import copy

import pytest

from table.col import col_sum
from table.grid import row_sum, transpose

SHIPPED = [[1, 2, 3], [4], [], [5, 6]]          # what tests/test_grid.py pins

LEAD_LONG = [[1, 2, 3, 4], [5], []]
LEAD_SHORT = [[1], [2, 3, 4]]                   # first row is NOT the longest
FIRST_ROW_EMPTY = [[], [7], [8, 9]]
NEGATIVES = [[-1, 2], [-3], [4, -5, 6]]
TALL_THIN = [[1], [2], [3], [4], [5], [6]]
WIDE_SHORT = [[1, 2, 3, 4, 5, 6]]
SINGLE = [[0]]
ALL_EMPTY = [[], [], []]

SHAPES = [LEAD_LONG, LEAD_SHORT, FIRST_ROW_EMPTY, NEGATIVES,
          TALL_THIN, WIDE_SHORT, SINGLE, ALL_EMPTY, SHIPPED]
FILLS = [0, -1, 7]


def reference_row_sum(grid, row):
    if row < 0:
        raise IndexError("negative row index")
    if row >= len(grid):
        return 0
    return sum(grid[row])


def reference_col_sum(grid, column):
    if column < 0:
        raise IndexError("negative column index")
    return sum(row[column] if column < len(row) else 0 for row in grid)


def reference_transpose(grid, fill=0):
    width = max((len(row) for row in grid), default=0)
    return [[row[i] if i < len(row) else fill for row in grid] for i in range(width)]


# --------------------------------------------------------------------------
# transpose: width from the longest row, padding from `fill`
# --------------------------------------------------------------------------
def test_transpose_uses_the_longest_row_not_the_first_one():
    # measured [[1, 2], [9, 3], [9, 4]]; an implementation reading the width off
    # grid[0] returns [[1, 2]] and is still green on the visible suite.
    assert transpose(LEAD_SHORT, 9) == [[1, 2], [9, 3], [9, 4]]
    assert transpose([[1, 2, 3, 4, 5], [6]], 8) == [[1, 6], [2, 8], [3, 8], [4, 8], [5, 8]]
    assert [len(column) for column in transpose(TALL_THIN, 0)] == [6]   # one column, six rows deep


def test_transpose_pads_with_the_fill_argument():
    # measured [[1, 3], [2, -1]] / [[1, 3], [2, 7]] / [[1, 3], [2, "X"]].
    # A `fill` that is ignored, coerced, or replaced by a literal 0 is not a fix.
    assert transpose([[1, 2], [3]], -1) == [[1, 3], [2, -1]]
    assert transpose([[1, 2], [3]], 7) == [[1, 3], [2, 7]]
    assert transpose([[1, 2], [3]], "X") == [[1, 3], [2, "X"]]


def test_transpose_default_fill_is_zero():
    # the public signature in the snapshot is transpose(grid, fill=0)
    assert transpose([[1, 2], [3]]) == [[1, 3], [2, 0]]


def test_transpose_keeps_leading_and_trailing_empty_rows_visible():
    # measured [[0, 7, 8], [0, 0, 9]] / [] / [] / [[1, 2, 3]]
    assert transpose(FIRST_ROW_EMPTY, 0) == [[0, 7, 8], [0, 0, 9]]
    assert transpose(ALL_EMPTY, 0) == []
    assert transpose([[]], 5) == []
    assert transpose([[1], [2], [3]], 0) == [[1, 2, 3]]


def test_transpose_does_not_mutate_the_grid_it_is_given():
    original = copy.deepcopy(SHIPPED)
    grid = copy.deepcopy(SHIPPED)
    result = transpose(grid, 0)
    assert grid == original, "transpose padded the caller's rows in place"
    assert result is not grid


def test_transpose_hands_back_fresh_rows():
    result = transpose(LEAD_LONG, 0)
    assert [type(column).__name__ for column in result] == ["list"] * 4
    assert len({id(column) for column in result}) == len(result)
    assert all(column is not row for column in result for row in LEAD_LONG)
    result[0].append("tampered")
    # measured on the repaired population: LEAD_LONG has 3 rows (the last one empty,
    # so it contributes the fill), hence 1, 5, 0.
    assert transpose(LEAD_LONG, 0)[0] == [1, 5, 0]


# --------------------------------------------------------------------------
# bounds follow the data, not the shipped fixture and not grid[0]
# --------------------------------------------------------------------------
def test_col_sum_bound_follows_the_grid_not_a_literal_width():
    # measured 6, 0, 5, 0. The shipped grid is 3 wide, so a literal comparison
    # against 3 cannot be seen by the visible suite.
    assert col_sum(WIDE_SHORT, 5) == 6
    assert col_sum(WIDE_SHORT, 6) == 0
    assert col_sum([[1, 2, 3, 4, 5], [9]], 4) == 5
    assert col_sum([[1, 2, 3, 4, 5], [9]], 5) == 0


def test_row_sum_bound_follows_the_grid_not_a_literal_height():
    # measured 5, 6, 0, 0, 10
    assert row_sum(TALL_THIN, 4) == 5
    assert row_sum(TALL_THIN, 5) == 6
    assert row_sum(TALL_THIN, 6) == 0
    assert row_sum(LEAD_LONG, 2) == 0          # an in-range empty row still sums to 0
    assert row_sum(LEAD_LONG, 0) == 10


def test_col_sum_is_not_anchored_on_the_first_row():
    # measured 3, 4, 2, 9 -- rows shorter than the asked-for column contribute 0.
    assert col_sum(LEAD_SHORT, 1) == 3
    assert col_sum(LEAD_SHORT, 2) == 4
    assert col_sum([[1, 2], [3]], 1) == 2
    assert col_sum(FIRST_ROW_EMPTY, 1) == 9


def test_empty_grid_is_a_zero_sum_not_a_crash():
    # measured 0 / 0 / 0, and both negative indices still raise. A
    # max(len(row) for row in grid) bound raises ValueError here, and an
    # `if not grid: return 0` placed in front of the guard stops rejecting them.
    assert col_sum([], 0) == 0
    assert col_sum([], 3) == 0
    assert row_sum([], 0) == 0
    with pytest.raises(IndexError):
        col_sum([], -1)
    with pytest.raises(IndexError):
        row_sum([], -1)


@pytest.mark.parametrize("column", [-1, -2, -3])
def test_negative_columns_are_rejected_whatever_the_grid(column):
    for grid in (SHIPPED, LEAD_SHORT, [[1, 2, 3]], FIRST_ROW_EMPTY):
        with pytest.raises(IndexError):
            col_sum(grid, column)


@pytest.mark.parametrize("row", [-1, -2, -6])
def test_negative_rows_are_rejected_whatever_the_grid(row):
    for grid in (SHIPPED, TALL_THIN, [[1, 2, 3]], ALL_EMPTY):
        with pytest.raises(IndexError):
            row_sum(grid, row)


def test_row_and_column_sums_do_not_mutate_the_grid():
    for grid in SHAPES:
        original = copy.deepcopy(grid)
        row_sum(grid, 0)
        col_sum(grid, 0)
        assert grid == original, f"{original} was rewritten by a sum"


# --------------------------------------------------------------------------
# cross-path agreement and a fixture-independent oracle
# --------------------------------------------------------------------------
@pytest.mark.parametrize("grid", SHAPES, ids=[str(i) for i in range(len(SHAPES))])
def test_all_three_functions_match_an_independent_reference(grid):
    for index in range(len(grid) + 2):
        assert row_sum(grid, index) == reference_row_sum(grid, index), index
    for index in range(max((len(row) for row in grid), default=0) + 2):
        assert col_sum(grid, index) == reference_col_sum(grid, index), index
    for fill in FILLS:
        assert transpose(grid, fill) == reference_transpose(grid, fill), fill


@pytest.mark.parametrize("grid", SHAPES, ids=[str(i) for i in range(len(SHAPES))])
def test_column_sum_and_transpose_describe_the_same_grid(grid):
    """col_sum(g, i) == sum(transpose(g)[i]); the two files cannot disagree."""
    for index in range(max((len(row) for row in grid), default=0)):
        column = transpose(grid, 0)[index]
        assert len(column) == len(grid), "a transposed column lost or gained rows"
        assert sum(column) == col_sum(grid, index)


def test_shipped_fixture_numbers_are_not_the_whole_story():
    # the visible values, re-derived, plus the same shape one row taller: a repair
    # that only makes the shipped literals come out right fails the second half.
    assert [row_sum(SHIPPED, i) for i in range(4)] == [6, 4, 0, 11]
    assert [col_sum(SHIPPED, i) for i in range(3)] == [10, 8, 3]
    taller = [[1, 2, 3], [4], [], [5, 6], [7, 8, 9, 10]]
    assert row_sum(taller, 4) == 34
    assert col_sum(taller, 3) == 10
    assert transpose(taller, 0)[-1] == [0, 0, 0, 0, 10]
