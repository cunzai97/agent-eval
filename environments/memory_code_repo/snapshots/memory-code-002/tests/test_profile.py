import pytest

from api.handler import handle
from cache.key import profile_key
from profile.service import clear_cache, get_profile


@pytest.fixture(autouse=True)
def clean_cache():
    clear_cache()


def repository():
    return {("a", "u1"): {"name": "Alice"}, ("b", "u1"): {"name": "Bob"},
            ("a", "u2"): {"name": "Amy"}}


def test_key_contains_both_identity_dimensions():
    assert profile_key("a", "u1") != profile_key("b", "u1")


def test_cross_tenant_reads_do_not_leak():
    repo = repository()
    assert get_profile("a", "u1", repo)["name"] == "Alice"
    assert get_profile("b", "u1", repo)["name"] == "Bob"


def test_handler_preserves_tenant_then_user_order():
    assert handle("b", "u1", repository())["name"] == "Bob"


def test_different_users_are_isolated():
    repo = repository()
    assert get_profile("a", "u1", repo)["name"] == "Alice"
    assert get_profile("a", "u2", repo)["name"] == "Amy"


def test_returned_profile_is_a_copy():
    repo = repository()
    value = get_profile("a", "u1", repo)
    value["name"] = "mutated"
    assert get_profile("a", "u1", repo)["name"] == "Alice"


def test_key_is_deterministic():
    assert profile_key("tenant-x", "user-y") == profile_key("tenant-x", "user-y")
