"""Inventory provider interface + value types.

`ItemStatus` is the normalized shape every provider must return, so the alert
formatter and `!check` command never see provider-specific JSON.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class ItemStatus:
    """Normalized result of looking up one SKU at one store."""

    item_id: str
    store_id: str
    found: bool                      # did the provider return any data at all?
    name: str | None = None
    price: float | None = None
    in_stock: bool | None = None
    inventory_quantity: int | None = None
    aisle: str | None = None
    bay: str | None = None
    store_name: str | None = None
    product_url: str | None = None
    error: str | None = None         # set when the lookup failed

    def is_penny_hit(self, threshold: float) -> bool:
        """True when this looks like a real, buyable penny deal at the store."""
        return (
            self.found
            and self.price is not None
            and self.price <= threshold
            and bool(self.inventory_quantity and self.inventory_quantity > 0)
        )


class InventoryProvider(abc.ABC):
    """Swappable backend for Problem B (is THIS sku penny'd at MY store?)."""

    @abc.abstractmethod
    async def check_item(self, item_id: str, store_id: str) -> ItemStatus:
        """Look up one item at one store. Must never raise for normal failures;
        return an ItemStatus with found=False / error set instead."""
        raise NotImplementedError

    async def close(self) -> None:  # optional cleanup hook
        return None


class NullInventoryProvider(InventoryProvider):
    """Phase 1 default: performs no external lookups.

    Lets the pipeline run end-to-end (alert on mention) without any paid API
    or store config. Swapped out for a real provider in Phase 2.
    """

    async def check_item(self, item_id: str, store_id: str) -> ItemStatus:
        return ItemStatus(
            item_id=item_id,
            store_id=store_id,
            found=False,
            error="inventory checking disabled (Phase 1 / INVENTORY_PROVIDER=none)",
        )
