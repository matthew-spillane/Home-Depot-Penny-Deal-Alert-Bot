"""SerpApi-backed inventory provider (Phase 2).

This is a *skeleton*: the request/response shapes are based on SerpApi's
Home Depot Product engine, but the exact field paths should be verified against
live responses before relying on them (HD/SerpApi change schemas). It is kept
behind the InventoryProvider interface so it can be hardened — or replaced with
BigBox API / a custom scraper — without touching the rest of the bot.

Docs: https://serpapi.com/home-depot-product-api

A short in-memory TTL cache avoids burning API quota when the same SKU is
mentioned across multiple posts in quick succession.
"""
from __future__ import annotations

import time

import aiohttp

from .base import InventoryProvider, ItemStatus

_SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
_CACHE_TTL_SECONDS = 600  # 10 minutes


class SerpApiInventoryProvider(InventoryProvider):
    def __init__(self, api_key: str, *, cache_ttl: int = _CACHE_TTL_SECONDS):
        self._api_key = api_key
        self._cache_ttl = cache_ttl
        self._cache: dict[tuple[str, str], tuple[float, ItemStatus]] = {}
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def check_item(self, item_id: str, store_id: str) -> ItemStatus:
        key = (item_id, store_id)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and (now - cached[0]) < self._cache_ttl:
            return cached[1]

        status = await self._lookup(item_id, store_id)
        self._cache[key] = (now, status)
        return status

    async def _lookup(self, item_id: str, store_id: str) -> ItemStatus:
        params = {
            "engine": "home_depot_product",
            "product_id": item_id,
            "store_id": store_id,
            "api_key": self._api_key,
        }
        try:
            session = await self._get_session()
            async with session.get(_SERPAPI_ENDPOINT, params=params, timeout=20) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    return ItemStatus(
                        item_id=item_id,
                        store_id=store_id,
                        found=False,
                        error=f"serpapi HTTP {resp.status}: {body[:200]}",
                    )
                data = await resp.json()
        except Exception as exc:  # network / timeout / json — degrade gracefully
            return ItemStatus(
                item_id=item_id, store_id=store_id, found=False, error=f"serpapi error: {exc}"
            )

        return self._parse(item_id, store_id, data)

    @staticmethod
    def _parse(item_id: str, store_id: str, data: dict) -> ItemStatus:
        """Map SerpApi JSON -> ItemStatus. VERIFY field paths against live data."""
        if "error" in data:
            return ItemStatus(
                item_id=item_id, store_id=store_id, found=False, error=str(data["error"])
            )

        product = data.get("product_results") or data.get("product") or {}
        if not product:
            return ItemStatus(item_id=item_id, store_id=store_id, found=False,
                              error="no product_results in response")

        price = _coerce_price(product.get("price"))
        store = product.get("store") or product.get("pickup") or {}
        qty = _coerce_int(store.get("quantity") or product.get("inventory_quantity"))
        in_stock = product.get("in_stock")
        if in_stock is None and qty is not None:
            in_stock = qty > 0

        return ItemStatus(
            item_id=item_id,
            store_id=store_id,
            found=True,
            name=product.get("title") or product.get("name"),
            price=price,
            in_stock=in_stock,
            inventory_quantity=qty,
            aisle=store.get("aisle"),
            bay=store.get("bay"),
            store_name=store.get("name") or store.get("store_name"),
            product_url=product.get("link") or product.get("product_link"),
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


def _coerce_price(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    # Strings like "$0.01" or "0.01"
    cleaned = str(raw).replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _coerce_int(raw) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None
