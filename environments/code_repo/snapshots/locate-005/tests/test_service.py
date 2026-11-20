from mem.cache import clear
from mem.service import load_item


def setup_function():
    clear()


def test_tenant_a_alone():
    assert load_item("tenant-a", "x")["name"] == "alpha"


def test_tenant_b_alone():
    assert load_item("tenant-b", "x")["name"] == "beta"


def test_same_id_in_two_tenants_is_isolated():
    assert load_item("tenant-a", "x")["name"] == "alpha"
    assert load_item("tenant-b", "x")["name"] == "beta"
