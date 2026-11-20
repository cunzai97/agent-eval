"""Hidden checks for snapshot fix-004.

The visible suite pins exactly one hand-picked record per behaviour, so a
parser that only survives those eight strings can stay green while the promised
semantics are still missing. These checks drive shapes the visible tests never
build: several quoted fields in one record, empty fields at either end, a
record that is empty all the way down (``""`` and ``","`` are one record with no
values, not "multiple records"), unquoted commas that must stay delimiters,
illegal fields that are only *almost* numeric, a blank line between two records,
long and negative sets behind the median, values with more precision than the
report shows, and cents that only survive exact Decimal arithmetic.

Two deliberate absences, because the prompt does not settle them:
- the rounding *mode* is never pinned (half-up and half-even implementations
  are both faithful to "固定两位小数"), so every expected number here is far
  from a .005 tie;
- no test string puts a space between a comma and an opening quote, or right
  after a closing quote. ``csv.reader`` and hand written splitters legitimately
  disagree there, and a strict ``csv.Error`` is not a promised rejection.
"""
import re
from decimal import Decimal

import pytest

from api.parse import parse_numbers
from api.summarize import summarize

TWO_PLACES = re.compile(r"-?\d+\.\d{2}\Z")


def test_several_quoted_fields_all_keep_their_thousands_separators():
    text = '"12,345.67","1,000.00", 20,"8,000.02"'
    assert parse_numbers(text) == [
        Decimal("12345.67"), Decimal("1000.00"), Decimal("20"), Decimal("8000.02")
    ]
    assert [type(v) for v in parse_numbers('"1,000.00",20')] == [Decimal, Decimal]
    assert summarize(text) == {
        "count": 4, "total": "21365.69", "mean": "5341.42", "median": "4500.01"
    }


def test_empty_fields_are_ignored_at_either_end_and_in_the_middle():
    text = ' , 7.50 ,,"1,050.00", '
    assert parse_numbers(text) == [Decimal("7.50"), Decimal("1050.00")]
    assert parse_numbers(",,,") == []
    assert parse_numbers("") == []
    assert summarize(text) == {
        "count": 2, "total": "1057.50", "mean": "528.75", "median": "528.75"
    }


def test_a_record_of_only_empty_fields_reports_zero_counts():
    assert summarize("   ") == {"count": 0, "total": "0.00", "mean": "0.00", "median": "0.00"}
    assert summarize(", , ,") == {"count": 0, "total": "0.00", "mean": "0.00", "median": "0.00"}
    assert summarize("") == {"count": 0, "total": "0.00", "mean": "0.00", "median": "0.00"}


def test_unquoted_commas_stay_field_delimiters():
    assert parse_numbers("12,345") == [Decimal("12"), Decimal("345")]
    assert summarize("12,345") == {
        "count": 2, "total": "357.00", "mean": "178.50", "median": "178.50"
    }


@pytest.mark.parametrize("text", [
    "1,2,3.4.5",
    "1,2,-",
    '"12x,00"',
    '"$1,200"',
    "total",
    "0.5, half",
])
def test_fields_that_are_not_plain_numbers_are_rejected(text):
    with pytest.raises(ValueError):
        parse_numbers(text)


def test_summarize_propagates_the_rejection_of_a_bad_record():
    with pytest.raises(ValueError):
        summarize("1,2,3.4.5")


@pytest.mark.parametrize("text", [
    "1,2\n\n3,4",
    "1\n2",
    '"1,200",3\n4,5\n6,7',
])
def test_more_than_one_record_is_rejected(text):
    with pytest.raises(ValueError):
        parse_numbers(text)
    with pytest.raises(ValueError):
        summarize(text)


def test_median_is_the_middle_value_of_the_ordered_set():
    assert summarize("8,-2,3.5,15,0.25") == {
        "count": 5, "total": "24.75", "mean": "4.95", "median": "3.50"
    }
    assert summarize("0.1,0.2,0.3,4.8,5.5,12")["median"] == "2.55"


def test_even_median_averages_the_two_centre_values_across_zero():
    assert summarize("-10,-5,5,10") == {
        "count": 4, "total": "0.00", "mean": "0.00", "median": "0.00"
    }
    assert summarize("1,2,3,4,5,100") == {
        "count": 6, "total": "115.00", "mean": "19.17", "median": "3.50"
    }


def test_a_single_value_record_is_its_own_median_and_mean():
    assert summarize("42") == {"count": 1, "total": "42.00", "mean": "42.00", "median": "42.00"}
    assert summarize('"7,000.05"') == {
        "count": 1, "total": "7000.05", "mean": "7000.05", "median": "7000.05"
    }


def test_a_long_record_is_counted_and_ordered_correctly():
    text = ",".join(["0.1"] * 10 + ["0.2"] * 7)
    assert summarize(text) == {
        "count": 17, "total": "2.40", "mean": "0.14", "median": "0.10"
    }


def test_two_decimal_places_are_fixed_and_never_grouped():
    result = summarize('"12,345.67","8,000.03"')
    assert result["count"] == 2
    for key in ("total", "mean", "median"):
        assert TWO_PLACES.match(result[key]), f"{key}={result[key]!r} is not fixed two decimals"
    assert result["total"] == "20345.70"
    assert result["mean"] == "10172.85"
    assert result["median"] == "10172.85"
    assert type(result["count"]) is int


def test_cents_survive_exact_decimal_arithmetic():
    text = '"1,000,000,000,000,000.10",0.05,0.05'
    assert parse_numbers(text) == [
        Decimal("1000000000000000.10"), Decimal("0.05"), Decimal("0.05")
    ]
    assert summarize(text) == {
        "count": 3, "total": "1000000000000000.20", "mean": "333333333333333.40", "median": "0.05"
    }


def test_zero_values_count_as_values_not_as_missing_fields():
    assert parse_numbers("0,0,0") == [Decimal("0"), Decimal("0"), Decimal("0")]
    assert summarize("0,0,0") == {"count": 3, "total": "0.00", "mean": "0.00", "median": "0.00"}
    assert summarize('0, ,"0.00", ')["count"] == 2


def test_the_mean_is_divided_by_the_values_that_survived_not_the_fields_seen():
    assert summarize('"1,200.00",,,') == {
        "count": 1, "total": "1200.00", "mean": "1200.00", "median": "1200.00"
    }


def test_a_signed_grouped_amount_keeps_its_sign_and_its_digits():
    assert parse_numbers('"-12,345.60",4.60') == [Decimal("-12345.60"), Decimal("4.60")]
    assert summarize('"-12,345.60",4.60') == {
        "count": 2, "total": "-12341.00", "mean": "-6170.50", "median": "-6170.50"
    }


def test_a_negative_mean_is_rounded_to_the_nearest_cent_and_not_truncated():
    assert summarize("-1,-2,-2") == {
        "count": 3, "total": "-5.00", "mean": "-1.67", "median": "-2.00"
    }
    assert summarize("-1,-1,-2")["mean"] == "-1.33"


def test_repeated_middle_values_do_not_move_the_median():
    assert summarize("7,7,7,7,7,0.02") == {
        "count": 6, "total": "35.02", "mean": "5.84", "median": "7.00"
    }


def test_a_quoted_empty_field_is_an_empty_field():
    assert parse_numbers('"",1,""') == [Decimal("1")]
    assert summarize('""') == {"count": 0, "total": "0.00", "mean": "0.00", "median": "0.00"}


def test_spaces_inside_a_quoted_field_are_not_part_of_the_number():
    assert parse_numbers('" 2.50 "') == [Decimal("2.50")]


def test_an_even_median_of_two_cents_values_keeps_both_cents():
    assert summarize('"1,000.01","1,000.03"') == {
        "count": 2, "total": "2000.04", "mean": "1000.02", "median": "1000.02"
    }


def test_a_long_record_has_a_true_middle_value():
    text = ",".join(str(index) for index in range(1, 26))
    assert summarize(text) == {
        "count": 25, "total": "325.00", "mean": "13.00", "median": "13.00"
    }
    assert summarize(",".join(str(index) for index in range(1, 25)))["median"] == "12.50"


def test_only_real_numbers_survive_the_parse():
    """A field that is not a number must be refused, not handed on as a value."""
    for text in ("1,nan", "NaN,1", '"inf",2', "1,-Infinity"):
        try:
            values = parse_numbers(text)
        except (ValueError, ArithmeticError):
            continue
        assert all(v.is_finite() for v in values), (
            f"{text!r} produced non-finite values: {values!r}"
        )


def test_rounding_happens_once_on_the_final_result_not_per_field():
    text = "1.004,2.004,3.004,4.004,5.004,6.004"
    assert parse_numbers(text) == [
        Decimal("1.004"), Decimal("2.004"), Decimal("3.004"),
        Decimal("4.004"), Decimal("5.004"), Decimal("6.004"),
    ]
    assert summarize(text) == {
        "count": 6, "total": "21.02", "mean": "3.50", "median": "3.50"
    }


def test_one_record_mixing_quoted_groups_a_negative_and_a_blank_field():
    text = '"12,345.67",-3,,"4.50","1,000.00"'
    assert parse_numbers(text) == [
        Decimal("12345.67"), Decimal("-3"), Decimal("4.50"), Decimal("1000.00")
    ]
    assert summarize(text) == {
        "count": 4, "total": "13347.17", "mean": "3336.79", "median": "502.25"
    }
