"""Tests for the inventory interface value types and provider factory."""
import asyncio

import pytest

from bot.inventory import ItemStatus, NullInventoryProvider, build_provider
from bot.inventory.serpapi_provider import _coerce_price, _coerce_int


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
