"""Tests for the inventory interface value types and provider factory."""
import asyncio

import pytest

from bot.inventory import ItemStatus, NullInventoryProvider, build_provider
from bot.inventory.serpapi_provider import (
    SerpApiInventoryProvider,
    _coerce_int,
    _coerce_price,
)


def test_penny_hit_logic():
    hit = ItemStatus("1", "2667", found=True, price=0.01, inventory_quantity=5)
    assert hit.is_penny_hit(1.00)

    too_pricey = ItemStatus("1", "2667", found=True, price=9.99, inventory_quantity=5)
    assert not too_pricey.is_penny_hit(1.00)

    out_of_stock = ItemStatus("1", "2667", found=True, price=0.01, inventory_quantity=0)
    assert not out_of_stock.is_penny_hit(1.00)

    not_found = ItemStatus("1", "2667", found=False)
    assert not not_found.is_penny_hit(1.00)


def test_null_provider_returns_unknown():
    prov = build_provider("none")
    assert isinstance(prov, NullInventoryProvider)
    status = asyncio.run(prov.check_item("1004567890", "2667"))
    assert not status.found
    assert status.error


def test_serpapi_requires_key():
    with pytest.raises(RuntimeError):
        build_provider("serpapi", serpapi_key=None)


def test_unknown_provider():
    with pytest.raises(RuntimeError):
        build_provider("nope")


def test_price_coercion():
    assert _coerce_price("$0.01") == 0.01
    assert _coerce_price("1,234.50") == 1234.50
    assert _coerce_price(0.01) == 0.01
    assert _coerce_price(None) is None
    assert _coerce_price("n/a") is None
    assert _coerce_int("5") == 5
    assert _coerce_int(None) is None


def test_default_status_is_not_a_failure():
    # A plain found=False (e.g. not stocked) must NOT count as an API failure.
    assert ItemStatus("1", "2667", found=False).lookup_failed is False


def test_parse_request_error_is_lookup_failure():
    # SerpApi signals bad key / quota / params via a top-level "error".
    st = SerpApiInventoryProvider._parse("123", "2667", {"error": "Invalid API key"})
    assert not st.found
    assert st.lookup_failed
    assert "Invalid API key" in st.error


def test_parse_missing_product_is_not_failure():
    # Valid response with no product => item not found, NOT a transient failure.
    st = SerpApiInventoryProvider._parse("123", "2667", {"search_metadata": {}})
    assert not st.found
    assert not st.lookup_failed


def test_parse_penny_hit_response():
    data = {
        "product_results": {
            "title": "Some Glitched Item",
            "price": "$0.01",
            "in_stock": True,
            "store": {"quantity": 7, "aisle": "12", "bay": "003", "name": "Lynn"},
            "link": "https://www.homedepot.com/p/x/123",
        }
    }
    st = SerpApiInventoryProvider._parse("123", "2667", data)
    assert st.found and not st.lookup_failed
    assert st.price == 0.01
    assert st.inventory_quantity == 7
    assert st.aisle == "12" and st.bay == "003"
    assert st.is_penny_hit(1.00)
