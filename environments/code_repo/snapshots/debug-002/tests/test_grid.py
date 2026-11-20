import pytest

from table.col import col_sum
from table.grid import row_sum, transpose


RAGGED = [[1, 2, 3], [4], [], [5, 6]]


def test_row_sum_regular_and_empty_row():
    assert row_sum(RAGGED, 0) == 6
    assert row_sum(RAGGED, 2) == 0


def test_row_sum_out_of_range_is_zero():
    assert row_sum(RAGGED, 99) == 0


def test_negative_row_index_is_rejected():
    with pytest.raises(IndexError):
        row_sum(RAGGED, -1)


def test_col_sum_treats_missing_cells_as_zero():
    assert col_sum(RAGGED, 0) == 10
    assert col_sum(RAGGED, 1) == 8
    assert col_sum(RAGGED, 2) == 3


def test_col_sum_out_of_range_is_zero():
    assert col_sum(RAGGED, 10) == 0


def test_negative_column_is_rejected():
    with pytest.raises(IndexError):
        col_sum(RAGGED, -1)


def test_transpose_preserves_ragged_tail_with_fill():
    assert transpose(RAGGED, fill=0) == [[1, 4, 0, 5], [2, 0, 0, 6], [3, 0, 0, 0]]


def test_transpose_empty_grid():
    assert transpose([]) == []
