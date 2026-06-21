"""Discord embed formatting for alerts and !check replies."""
from __future__ import annotations

from datetime import datetime, timezone

import discord

from .inventory import ItemStatus
from .reddit_watcher import Mention

HD_PRODUCT_URL = "https://www.homedepot.com/p/{item_id}"


def mention_embed(mention: Mention) -> discord.Embed:
    """Phase 1 alert: 'possible penny deal mentioned' — no store verification."""
    ids = mention.extraction.internet_ids
    title = "🟡 Possible penny deal mentioned"
    embed = discord.Embed(
        title=title,
        description=mention.title[:4000],
        color=0xF2A900,
        timestamp=datetime.now(timezone.utc),
    )
    if ids:
        sku_lines = "\n".join(
            f"`{i}` — [HD link]({HD_PRODUCT_URL.format(item_id=i)})" for i in ids
        )
        embed.add_field(name="Extracted SKU(s)", value=sku_lines, inline=False)
    if mention.extraction.model_numbers:
        embed.add_field(
            name="Model #(s)",
            value=", ".join(f"`{m}`" for m in mention.extraction.model_numbers),
            inline=False,
        )
    embed.add_field(
        name="Source",
        value=f"[r/{mention.subreddit} {mention.kind}]({mention.permalink})"
        + (f" by u/{mention.author}" if mention.author else ""),
        inline=False,
    )
    embed.set_footer(text="Phase 1 • not verified at your store yet")
    return embed


def status_embed(status: ItemStatus, *, source: str | None = None,
                 threshold: float | None = None) -> discord.Embed:
    """Phase 2 alert / !check reply: a store-verified inventory result."""
    is_hit = threshold is not None and status.is_penny_hit(threshold)
    if not status.found:
        if status.lookup_failed:
            title = "🔴 Lookup failed"
            color = 0xE74C3C
        else:
            title = "⚪ No store data"
            color = 0x808080
        return discord.Embed(
            title=title,
            description=f"`{status.item_id}` — {status.error or 'no result'}",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )

    color = 0x2ECC71 if is_hit else 0x3498DB
    flag = "🟢 PENNY HIT" if is_hit else "🔵 Store result"
    embed = discord.Embed(
        title=f"{flag} — {status.name or status.item_id}",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="SKU / Item #", value=f"`{status.item_id}`", inline=True)
    if status.price is not None:
        embed.add_field(name="Price", value=f"${status.price:,.2f}", inline=True)
    if status.inventory_quantity is not None:
        embed.add_field(name="Qty on hand", value=str(status.inventory_quantity), inline=True)
    if status.store_name:
        embed.add_field(name="Store", value=status.store_name, inline=True)
    location = " / ".join(p for p in (status.aisle, status.bay) if p)
    if location:
        embed.add_field(name="Aisle / Bay", value=location, inline=True)
    url = status.product_url or HD_PRODUCT_URL.format(item_id=status.item_id)
    embed.add_field(name="Link", value=f"[homedepot.com]({url})", inline=False)
    if source:
        embed.add_field(name="Source", value=source, inline=False)
    return embed
