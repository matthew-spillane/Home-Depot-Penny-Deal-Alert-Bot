#!/usr/bin/env python3
"""Probe SerpApi for one Home Depot SKU and validate the _parse mapping.

This is the tool for closing the one open risk in Phase 2: the field paths in
`bot/inventory/serpapi_provider._parse` are best-effort guesses until checked
against a real response. Run this against a known SKU at your store and compare:

    RAW JSON  (what SerpApi actually returns)
        vs
    MAPPED    (what ItemStatus / the penny-hit gate sees)

If the mapped values look wrong, edit `_parse` and re-run until they line up.

Usage:
    export SERPAPI_KEY=...            # or set it in .env
    python -m scripts.probe_serpapi <item_id> [--store 2667]
    python -m scripts.probe_serpapi 312345678 --store 2667
    python -m scripts.probe_serpapi 312345678 --engine home_depot_product \\
        --param delivery_zip=01902 --raw-only

Flags let you experiment with the engine name and request params without
editing code, since the engine/params themselves are unverified guesses.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict

from dotenv import load_dotenv

# Reuse the exact endpoint + parser the bot uses, so a green result here means
# the bot itself will map correctly.
from bot.inventory.serpapi_provider import _SERPAPI_ENDPOINT, SerpApiInventoryProvider

load_dotenv()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Probe SerpApi for one HD SKU.")
    p.add_argument("item_id", help="Home Depot internet/item number, e.g. 312345678")
    p.add_argument("--store", default=os.environ.get("DEFAULT_STORE_ID", ""),
                   help="Store id (defaults to $DEFAULT_STORE_ID)")
    p.add_argument("--engine", default="home_depot_product",
                   help="SerpApi engine name (default: home_depot_product)")
    p.add_argument("--key", default=os.environ.get("SERPAPI_KEY", ""),
                   help="SerpApi key (defaults to $SERPAPI_KEY)")
    p.add_argument("--param", action="append", default=[], metavar="K=V",
                   help="Extra request param(s), repeatable. e.g. --param delivery_zip=01902")
    p.add_argument("--raw-only", action="store_true",
                   help="Print only the raw JSON, skip the mapping preview")
    p.add_argument("--threshold", type=float,
                   default=float(os.environ.get("PENNY_PRICE_THRESHOLD", "1.00")),
                   help="Penny-hit threshold for the mapping preview (default 1.00)")
    return p.parse_args(argv)


async def _fetch(args: argparse.Namespace) -> dict:
    import aiohttp

    params = {
        "engine": args.engine,
        "product_id": args.item_id,
        "api_key": args.key,
    }
    if args.store:
        params["store_id"] = args.store
    for raw in args.param:
        if "=" not in raw:
            sys.exit(f"--param must be K=V, got: {raw!r}")
        k, v = raw.split("=", 1)
        params[k] = v

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(_SERPAPI_ENDPOINT, params=params) as resp:
            text = await resp.text()
            if resp.status != 200:
                print(f"HTTP {resp.status} from SerpApi:\n{text[:1000]}", file=sys.stderr)
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                sys.exit(f"Response was not JSON:\n{text[:1000]}")


def _print_mapping(item_id: str, store_id: str, data: dict, threshold: float) -> None:
    status = SerpApiInventoryProvider._parse(item_id, store_id, data)
    print("\n" + "=" * 60)
    print("MAPPED ItemStatus (what the bot's penny-hit gate sees):")
    print("=" * 60)
    for k, v in asdict(status).items():
        print(f"  {k:20} = {v!r}")
    verdict = "✅ PENNY HIT" if status.is_penny_hit(threshold) else "— not a hit"
    print(f"\n  is_penny_hit(threshold=${threshold:.2f}) -> {verdict}")
    if status.found and status.price is None:
        print("\n  ⚠️  price did not map — check the price path in _parse")
    if status.found and status.inventory_quantity is None:
        print("  ⚠️  quantity did not map — check the store/quantity path in _parse")


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if not args.key:
        sys.exit("No SerpApi key. Set SERPAPI_KEY (env/.env) or pass --key.")

    data = asyncio.run(_fetch(args))

    print("=" * 60)
    print("RAW SerpApi response:")
    print("=" * 60)
    print(json.dumps(data, indent=2)[:20000])

    if not args.raw_only:
        _print_mapping(args.item_id, args.store, data, args.threshold)


if __name__ == "__main__":
    main()
