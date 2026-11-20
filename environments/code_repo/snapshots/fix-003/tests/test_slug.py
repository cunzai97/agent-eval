import pytest

from text.slug import slugify


def test_accents_are_folded():
    assert slugify("Crème Brûlée") == "creme-brulee"


def test_casefold_handles_german_sharp_s():
    assert slugify("Straße") == "strasse"


def test_repeated_separators_collapse():
    assert slugify("Hello___  World") == "hello-world"


def test_apostrophe_is_removed_without_splitting_word():
    assert slugify("Don't Stop") == "dont-stop"


def test_leading_and_trailing_separators_are_removed():
    assert slugify(" -- Alpha -- ") == "alpha"


def test_max_length_does_not_leave_partial_word_or_separator():
    assert slugify("alpha beta gamma", max_length=12) == "alpha-beta"


def test_empty_after_normalization_is_rejected():
    with pytest.raises(ValueError):
        slugify("---")


def test_non_string_is_rejected():
    with pytest.raises(TypeError):
        slugify(None)
