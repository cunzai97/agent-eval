import pytest

from depkit.resolve import ResolutionError, install_order, pick
from depkit.version import compare, parse, satisfies


def test_parse_plain_version():
    assert parse("1.2.3") == (1, 2, 3, None)


def test_parse_prerelease_is_tuple():
    assert parse("1.0.0-alpha.1") == (1, 0, 0, ("alpha", "1"))


def test_parse_rejects_bad_input():
    for text in ("1.2", "1.2.x", "", "1.2.3-"):
        with pytest.raises(ValueError):
            parse(text)


def test_compare_numeric_fields():
    assert compare("1.10.0", "1.9.0") == 1
    assert compare("2.0.0", "2.0.0") == 0


def test_release_is_higher_than_prerelease():
    assert compare("1.0.0", "1.0.0-alpha") == 1


def test_prerelease_numeric_identifiers():
    assert compare("1.0.0-alpha.10", "1.0.0-alpha.9") == 1


def test_satisfies_includes_boundaries():
    assert satisfies("1.2.0", ">=1.2.0") is True
    assert satisfies("2.0.0", "<2.0.0") is False


def test_pick_prefers_highest_numeric_version():
    assert pick({"a": ["1.9.0", "1.10.0"]}, {"a": [">=1.0.0"]}) == {"a": "1.10.0"}


def test_pick_reports_conflicts():
    with pytest.raises(ResolutionError):
        pick({"a": ["1.0.0"]}, {"a": [">=2.0.0"]})


def test_install_order_places_dependencies_first():
    deps = {"app": ["web", "db"], "web": ["core"], "db": ["core"], "core": []}
    assert install_order(deps) == ["core", "db", "web", "app"]


def test_install_order_rejects_cycles():
    with pytest.raises(ResolutionError):
        install_order({"a": ["b"], "b": ["a"]})
