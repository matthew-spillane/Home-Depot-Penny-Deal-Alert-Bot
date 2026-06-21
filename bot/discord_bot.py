"""Discord bot — the single long-running process.

Runs everything in one event loop (one Railway service):
  * a `tasks.loop` polls Reddit every REDDIT_POLL_SECONDS and posts alerts,
  * `!check <SKU|url>` runs the inventory checker on demand.

A bot (not a webhook) is used specifically so on-demand commands work.
"""
from __future__ import annotations

import logging
import re

import discord
from discord.ext import commands, tasks

from . import alerts, sku_extractor
from .config_types import Config
from .inventory import InventoryProvider, build_provider
from .reddit_watcher import RedditWatcher
from .state import State

log = logging.getLogger(__name__)

# Pull an item id out of "!check <arg>" whether the user passes a bare number
# or a full homedepot.com product URL.
_URL_ID_RE = re.compile(r"homedepot\.com/p/[^\s/]+/(\d{6,12})", re.IGNORECASE)
_BARE_ID_RE = re.compile(r"\b(\d{6,12})\b")


class PennyBot(commands.Bot):
    def __init__(self, cfg: Config):
        intents = discord.Intents.default()
        intents.message_content = True  # required to read !check command args
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.cfg = cfg
        self.state = State(cfg.db_path)
        self.watcher = RedditWatcher(
            client_id=cfg.reddit_client_id,
            client_secret=cfg.reddit_client_secret,
            user_agent=cfg.reddit_user_agent,
            subreddits=cfg.subreddits,
            fetch_limit=cfg.reddit_fetch_limit,
        )
        self.inventory: InventoryProvider = build_provider(
            cfg.inventory_provider, serpapi_key=cfg.serpapi_key
        )
        self._consecutive_poll_failures = 0
        self.add_commands()

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def setup_hook(self) -> None:
        self.poll_reddit.change_interval(seconds=self.cfg.reddit_poll_seconds)
        self.poll_reddit.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        log.info("Watching r/%s every %ss", self.watcher.subreddit_str, self.cfg.reddit_poll_seconds)

    async def close(self) -> None:
        self.poll_reddit.cancel()
        await self.watcher.close()
        await self.inventory.close()
        self.state.close()
        await super().close()

    # ── Reddit poll loop ─────────────────────────────────────────────────────
    @tasks.loop(seconds=90)
    async def poll_reddit(self) -> None:
        try:
            mentions = await self.watcher.poll(self.state.is_seen)
            self._consecutive_poll_failures = 0
        except Exception:
            self._consecutive_poll_failures += 1
            log.exception("poll failed (%d in a row)", self._consecutive_poll_failures)
            # Phase 3 failure alerting: DM the owner if it keeps failing.
            if self._consecutive_poll_failures in (3, 10) and self.cfg.discord_owner_id:
                await self._dm_owner(
                    f"⚠️ Reddit poll has failed {self._consecutive_poll_failures} times in a row."
                )
            return

        channel = self.get_channel(self.cfg.discord_alerts_channel_id)
        if channel is None:
            log.error("Alerts channel %s not found / bot lacks access",
                      self.cfg.discord_alerts_channel_id)
            return

        for mention in mentions:
            try:
                await channel.send(embed=alerts.mention_embed(mention))
                self.state.mark_seen(mention.reddit_id)
            except Exception:
                log.exception("Failed to post alert for %s", mention.reddit_id)
                # leave unseen so we retry next cycle

    @poll_reddit.before_loop
    async def _before_poll(self) -> None:
        await self.wait_until_ready()

    async def _dm_owner(self, text: str) -> None:
        if not self.cfg.discord_owner_id:
            return
        try:
            user = await self.fetch_user(self.cfg.discord_owner_id)
            await user.send(text)
        except Exception:
            log.exception("Could not DM owner")

    # ── commands ─────────────────────────────────────────────────────────────
    def add_commands(self) -> None:
        @self.command(name="check")
        async def check(ctx: commands.Context, *, arg: str = ""):
            """!check <SKU or homedepot.com product URL> — verify at your store."""
            if self.cfg.discord_commands_channel_id and (
                ctx.channel.id not in (
                    self.cfg.discord_commands_channel_id,
                    self.cfg.discord_alerts_channel_id,
                )
            ):
                return

            item_id = self._parse_item_id(arg)
            if not item_id:
                await ctx.reply("Usage: `!check <SKU>` or `!check <homedepot.com product URL>`")
                return

            store_id = self.cfg.default_store_id or ""
            if not store_id:
                await ctx.reply(
                    f"Found item `{item_id}`, but no `DEFAULT_STORE_ID` is configured, "
                    f"so I can't check store inventory yet (Phase 2)."
                )
                return

            async with ctx.typing():
                status = await self.inventory.check_item(item_id, store_id)
            await ctx.reply(
                embed=alerts.status_embed(
                    status,
                    source=f"requested by {ctx.author.mention}",
                    threshold=self.cfg.penny_price_threshold,
                )
            )

        @self.command(name="ping")
        async def ping(ctx: commands.Context):
            await ctx.reply("pong 🪙")

    @staticmethod
    def _parse_item_id(arg: str) -> str | None:
        arg = (arg or "").strip()
        if not arg:
            return None
        m = _URL_ID_RE.search(arg)
        if m:
            return m.group(1)
        m = _BARE_ID_RE.search(arg)
        return m.group(1) if m else None
