"""Store inventory checking — the swappable Problem B boundary.

The whole point of this package is that the rest of the codebase only ever
touches `InventoryProvider.check_item(...)` and `ItemStatus`. The underlying
data source (SerpApi today, BigBox API or a custom HD scraper tomorrow) can be
swapped without touching the Reddit pipeline or the Discord bot.

Phase 1 ships the `none` provider (returns UNKNOWN, no external calls). Phase 2
wires in SerpApi.
"""
from __future__ import annotations

from .base import InventoryProvider, ItemStatus, NullInventoryProvider


def build_provider(name: str, *, serpapi_key: str | None = None) -> InventoryProvider:
    """Factory: pick an inventory provider by config name."""
    name = (name or "none").lower()
    if name in ("none", "", "null"):
        return NullInventoryProvider()
    if name == "serpapi":
        # Imported lazily so Phase 1 doesn't require the provider's deps/key.
        from .serpapi_provider import SerpApiInventoryProvider

        if not serpapi_key:
            raise RuntimeError("INVENTORY_PROVIDER=serpapi requires SERPAPI_KEY")
        return SerpApiInventoryProvider(serpapi_key)
    raise RuntimeError(f"Unknown INVENTORY_PROVIDER: {name!r}")


__all__ = [
    "InventoryProvider",
    "ItemStatus",
    "NullInventoryProvider",
    "build_provider",
]
