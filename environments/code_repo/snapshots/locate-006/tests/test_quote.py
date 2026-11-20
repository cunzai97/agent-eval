from checkout.service import quote
from pricing.cache import clear


def setup_function():
    clear()


def test_usd_alone():
    assert quote("book", "USD", "US") == 100.0


def test_eur_alone():
    assert quote("book", "EUR", "DE") == 80.0


def test_sequential_currencies_do_not_contaminate_each_other():
    assert quote("pen", "USD", "US") == 10.0
    assert quote("pen", "EUR", "DE") == 8.0


def test_discount_is_not_the_cause():
    assert quote("book", "USD", "US", tier="gold") == 90.0
