"""Hidden checks for dep_graph: version ordering, range matching and resolution."""
import pytest

from depkit.resolve import ResolutionError, install_order, pick
from depkit.version import compare, parse, satisfies


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0.0.0", (0, 0, 0, None)),
        ("1.2.3", (1, 2, 3, None)),
        ("10.20.30", (10, 20, 30, None)),
        ("1.0.0-alpha", (1, 0, 0, ("alpha",))),
        ("1.0.0-alpha.1", (1, 0, 0, ("alpha", "1"))),
        ("1.0.0-rc.1.2", (1, 0, 0, ("rc", "1", "2"))),
    ],
)
def test_parse_shapes(text, expected):
    assert parse(text) == expected


@pytest.mark.parametrize(
    "text", ["", "1", "1.2", "1.2.3.4", "v1.2.3", "1.2.3-", "1.2.3+meta", "1..3", "1.2.x"]
)
def test_parse_rejects_malformed(text):
    with pytest.raises(ValueError):
        parse(text)


def test_parse_rejects_non_string():
    for value in (123, None, 1.5, b"1.2.3"):
        with pytest.raises(ValueError):
            parse(value)


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("1.2.3", "1.2.3", 0),
        ("1.2.4", "1.2.3", 1),
        ("1.3.0", "1.2.9", 1),
        ("2.0.0", "1.99.99", 1),
        ("1.9.0", "1.10.0", -1),
        ("1.0.0-alpha", "1.0.0", -1),
        ("1.0.0", "1.0.0-alpha", 1),
        ("1.0.0-alpha", "1.0.0-alpha.1", -1),
        ("1.0.0-alpha.1", "1.0.0-alpha", 1),
        ("1.0.0-alpha.9", "1.0.0-alpha.10", -1),
        ("1.0.0-alpha.10", "1.0.0-alpha.9", 1),
        ("1.0.0-alpha", "1.0.0-beta", -1),
        ("1.0.0-beta.2", "1.0.0-beta.11", -1),
        ("1.0.0-1", "1.0.0-alpha", -1),
        ("1.0.0-alpha", "1.0.0-1", 1),
    ],
)
def test_compare_semantics(left, right, expected):
    assert compare(left, right) == expected


def test_compare_rejects_invalid_versions():
    with pytest.raises(ValueError):
        compare("1.0", "1.0.0")


@pytest.mark.parametrize(
    "version,spec,expected",
    [
        ("1.2.0", ">=1.2.0", True),
        ("1.1.9", ">=1.2.0", False),
        ("1.2.0", ">1.2.0", False),
        ("1.2.1", ">1.2.0", True),
        ("2.0.0", "<=2.0.0", True),
        ("2.0.1", "<=2.0.0", False),
        ("2.0.0", "<2.0.0", False),
        ("1.2.3", "==1.2.3", True),
        ("1.2.4", "==1.2.3", False),
        ("1.2.3", "!=1.2.3", False),
        ("1.2.4", "!=1.2.3", True),
        ("1.0.0", ">=1.0.0-alpha", True),
    ],
)
def test_satisfies_operators(version, spec, expected):
    assert satisfies(version, spec) is expected


def test_satisfies_tolerates_whitespace():
    assert satisfies("1.2.3", "  >= 1.2.0  ") is True


@pytest.mark.parametrize("spec", ["~1.2.0", "^1.2.0", "1.2.3", "=>1.2.0"])
def test_satisfies_rejects_unknown_operator(spec):
    with pytest.raises(ValueError):
        satisfies("1.2.3", spec)


def test_pick_chooses_highest_satisfying_version():
    index = {"lib": ["1.9.0", "1.10.0", "1.2.0", "2.0.0"]}
    assert pick(index, {"lib": [">=1.0.0", "<2.0.0"]}) == {"lib": "1.10.0"}


def test_pick_prefers_release_over_prerelease():
    index = {"lib": ["2.0.0-rc.1", "2.0.0"]}
    assert pick(index, {"lib": [">=1.0.0"]}) == {"lib": "2.0.0"}


def test_pick_ignores_unsatisfying_candidates():
    index = {"lib": ["1.0.0", "1.4.0", "2.0.0"]}
    assert pick(index, {"lib": ["<2.0.0", ">=1.4.0"]}) == {"lib": "1.4.0"}


def test_pick_handles_empty_requirements():
    assert pick({"lib": ["1.0.0"]}, {}) == {}


def test_pick_raises_for_unknown_package():
    with pytest.raises(ResolutionError):
        pick({"lib": ["1.0.0"]}, {"missing": [">=1.0.0"]})


def test_pick_raises_for_empty_candidate_list():
    with pytest.raises(ResolutionError):
        pick({"lib": []}, {"lib": [">=1.0.0"]})


def test_pick_raises_for_conflicting_specs():
    with pytest.raises(ResolutionError):
        pick({"lib": ["1.0.0", "1.5.0"]}, {"lib": [">=1.5.0", "<1.2.0"]})


def test_install_order_follows_chain():
    assert install_order({"a": ["b"], "b": ["c"], "c": []}) == ["c", "b", "a"]


def test_install_order_handles_diamond():
    deps = {"app": ["web", "db"], "web": ["core"], "db": ["core"], "core": []}
    assert install_order(deps) == ["core", "db", "web", "app"]


def test_install_order_sorts_siblings_by_name():
    assert install_order({"root": ["zeta", "alpha", "mike"], "zeta": [], "alpha": [], "mike": []}) == [
        "alpha",
        "mike",
        "zeta",
        "root",
    ]


def test_install_order_accepts_external_leaf():
    assert install_order({"app": ["external"]}) == ["external", "app"]


@pytest.mark.parametrize(
    "deps",
    [
        {"a": ["b"], "b": ["a"]},
        {"a": ["a"]},
        {"a": ["b"], "b": ["c"], "c": ["a"]},
    ],
)
def test_install_order_rejects_cycles(deps):
    with pytest.raises(ResolutionError):
        install_order(deps)


def test_install_order_empty_graph():
    assert install_order({}) == []
