import pytest

from money.convert import to_minor_units


def test_usd_common_values():
    assert to_minor_units("19.99", "USD") == 1999
    assert to_minor_units("0.29", "USD") == 29


def test_usd_rounds_half_up():
    assert to_minor_units("1,234.565", "USD") == 123457


def test_jpy_has_no_fractional_minor_unit():
    assert to_minor_units("10.5", "JPY") == 11


def test_kwd_has_three_minor_digits():
    assert to_minor_units("1.2345", "KWD") == 1235


def test_accounting_negative_and_currency_symbol():
    assert to_minor_units("($2.50)", "USD") == -250


def test_invalid_number_is_rejected():
    with pytest.raises(ValueError):
        to_minor_units("not-money", "USD")


def test_unknown_currency_is_rejected():
    with pytest.raises(ValueError):
        to_minor_units("1", "ABC")


def test_decimal_input_does_not_round_trip_through_binary_float():
    assert to_minor_units("1000000000000.01", "USD") == 100000000000001
