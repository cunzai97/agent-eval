from decimal import Decimal

import pytest

from api.parse import parse_numbers
from api.summarize import summarize


def test_csv_quotes_and_thousands_separator():
    assert parse_numbers('"1,200.50", 2.5, , -3') == [Decimal("1200.50"), Decimal("2.5"), Decimal("-3")]


def test_decimal_precision_is_preserved():
    assert parse_numbers("0.1,0.2") == [Decimal("0.1"), Decimal("0.2")]


def test_invalid_field_reports_error():
    with pytest.raises(ValueError):
        parse_numbers("1,bad,3")


def test_multiple_csv_records_are_rejected():
    with pytest.raises(ValueError):
        parse_numbers("1,2\n3,4")


def test_summary_uses_exact_decimal_arithmetic_and_true_median():
    assert summarize('"1,200.50",2.5,-3') == {
        "count": 3, "total": "1200.00", "mean": "400.00", "median": "2.50"
    }


def test_even_median_averages_middle_pair():
    assert summarize("1,2,10,20")["median"] == "6.00"


def test_empty_input():
    assert summarize(" , ") == {"count": 0, "total": "0.00", "mean": "0.00", "median": "0.00"}


def test_point_one_plus_point_two_formats_exactly():
    assert summarize("0.1,0.2")["total"] == "0.30"
