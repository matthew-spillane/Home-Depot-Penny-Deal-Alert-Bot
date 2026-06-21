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

from . import alerts
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
        # Phase 3: optional LLM fallback for messy posts (None when disabled).
        self.llm = None
        if cfg.llm_fallback_enabled and cfg.anthropic_api_key:
            from .llm_extractor import LLMExtractor

            self.llm = LLMExtractor(
                api_key=cfg.anthropic_api_key,
                model=cfg.llm_model,
                max_calls_per_poll=cfg.llm_max_calls_per_poll,
            )
        # Phase 3 stretch: optional web dashboard (None unless a port is set).
        self.dashboard = None
        if cfg.dashboard_port:
            from .dashboard import Dashboard

            self.dashboard = Dashboard(
                self.state, port=cfg.dashboard_port,
                token=cfg.dashboard_token, store_ids=self.store_ids,
            )
        self._consecutive_poll_failures = 0
        self._consecutive_inventory_failures = 0
        self.add_commands()

    def store_ids(self) -> list[str]:
        """Effective set of stores to check: config (DEFAULT_STORE_ID + STORE_IDS)
        unioned with any added at runtime via !addstore. Order-stable, de-duped."""
        out: list[str] = []
        for sid in [self.cfg.default_store_id, *self.cfg.extra_store_ids,
                    *self.state.list_stores()]:
            if sid and sid not in out:
                out.append(sid)
        return out

    @property
    def verification_active(self) -> bool:
        """Phase 2/3 verification is live when a real inventory provider AND at
        least one store are configured.

        When False we fall back to Phase 1 behavior (post every mention,
        unverified) so the bot is still useful before SerpApi/store config.
        """
        return (
            self.cfg.inventory_provider not in ("none", "", "null")
            and bool(self.store_ids())
        )

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def setup_hook(self) -> None:
        self.poll_reddit.change_interval(seconds=self.cfg.reddit_poll_seconds)
        self.poll_reddit.start()
        if self.dashboard:
            await self.dashboard.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        log.info("Watching r/%s every %ss", self.watcher.subreddit_str, self.cfg.reddit_poll_seconds)
        if self.verification_active:
            log.info(
                "Verification active: checking stores %s via '%s' (threshold $%.2f)",
                ", ".join(self.store_ids()), self.cfg.inventory_provider,
                self.cfg.penny_price_threshold,
            )
        else:
            log.info("Phase 1 mode: posting unverified mentions "
                     "(set INVENTORY_PROVIDER + DEFAULT_STORE_ID to enable verification)")
        log.info("LLM SKU fallback: %s",
                 f"on ({self.cfg.llm_model})" if self.llm else "off")
        log.info("Web dashboard: %s",
                 f"on (:{self.cfg.dashboard_port})" if self.dashboard else "off")

    async def close(self) -> None:
        self.poll_reddit.cancel()
        if self.dashboard:
            await self.dashboard.stop()
        await self.watcher.close()
        await self.inventory.close()
        if self.llm:
            await self.llm.close()
        self.state.close()
        await super().close()

    # ── Reddit poll loop ─────────────────────────────────────────────────────
    @tasks.loop(seconds=90)
    async def poll_reddit(self) -> None:
        llm_extract = None
        if self.llm:
            self.llm.reset_budget()  # refill the per-poll LLM call budget
            llm_extract = self.llm.extract_item_ids
        try:
            mentions = await self.watcher.poll(self.state.is_seen, llm_extract)
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
                if self.verification_active:
                    await self._handle_verified(mention, channel)
                else:
                    await channel.send(embed=alerts.mention_embed(mention))
                    self.state.log_alert(
                        kind="mention",
                        sku=(mention.extraction.internet_ids[0]
                             if mention.extraction.internet_ids else None),
                        name=mention.title[:200],
                        subreddit=f"r/{mention.subreddit}",
                        source_url=mention.permalink,
                    )
                # Mark seen once handled so we don't re-process every cycle. In
                # verified mode a SKU that isn't penny'd *right now* won't be
                # re-checked later — an accepted v1 cost/noise tradeoff.
                self.state.mark_seen(mention.reddit_id)
            except Exception:
                log.exception("Failed to handle mention %s", mention.reddit_id)
                # leave unseen so we retry next cycle

    async def _handle_verified(self, mention, channel) -> None:
        """Phase 2/3: only alert when a mentioned SKU is a real penny hit
        (price <= threshold AND in stock) at one of the watched stores."""
        threshold = self.cfg.penny_price_threshold
        source = f"[r/{mention.subreddit} {mention.kind}]({mention.permalink})"

        for sku in mention.extraction.all_skus:
            for store_id in self.store_ids():
                # Per-(SKU, store) daily dedup so the same hit mentioned across
                # many posts only pings once a day, per store.
                if self.state.already_alerted_today(sku, store_id):
                    continue

                status = await self.inventory.check_item(sku, store_id)
                self._track_inventory_health(status)

                if status.is_penny_hit(threshold):
                    await channel.send(
                        embed=alerts.status_embed(status, source=source, threshold=threshold)
                    )
                    self.state.record_alert(sku, store_id)
                    self.state.log_alert(
                        kind="hit", sku=sku, store_id=store_id,
                        name=status.name, price=status.price,
                        quantity=status.inventory_quantity,
                        subreddit=f"r/{mention.subreddit}",
                        source_url=mention.permalink,
                    )
                else:
                    log.info("SKU %s @ %s not a hit (%s)", sku, store_id,
                             status.error or f"price={status.price} qty={status.inventory_quantity}")

    def _track_inventory_health(self, status) -> None:
        """DM the owner if the inventory API starts failing repeatedly, so a
        silently-broken provider gets noticed fast (the #1 reliability risk)."""
        if status.lookup_failed:
            self._consecutive_inventory_failures += 1
            log.warning("Inventory lookup failed (%d in a row): %s",
                        self._consecutive_inventory_failures, status.error)
            if self._consecutive_inventory_failures in (5, 25) and self.cfg.discord_owner_id:
                # Fire-and-forget so a failing DM can't break the poll loop.
                self.loop.create_task(self._dm_owner(
                    f"⚠️ Inventory lookups have failed "
                    f"{self._consecutive_inventory_failures} times in a row. "
                    f"Last error: {status.error}"
                ))
        else:
            self._consecutive_inventory_failures = 0

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

    def _in_command_channel(self, ctx: commands.Context) -> bool:
        if not self.cfg.discord_commands_channel_id:
            return True
        return ctx.channel.id in (
            self.cfg.discord_commands_channel_id,
            self.cfg.discord_alerts_channel_id,
        )

    # ── commands ─────────────────────────────────────────────────────────────
    def add_commands(self) -> None:
        @self.command(name="check")
        async def check(ctx: commands.Context, *args: str):
            """!check <SKU|url> [store_id] — verify at a store (default: first)."""
            if not self._in_command_channel(ctx):
                return

            item_id = self._parse_item_id(" ".join(args))
            if not item_id:
                await ctx.reply("Usage: `!check <SKU>` or `!check <homedepot.com product URL> [store_id]`")
                return

            if self.cfg.inventory_provider in ("none", "", "null"):
                await ctx.reply(
                    f"Found item `{item_id}`, but inventory checking is off "
                    f"(`INVENTORY_PROVIDER=none`). Set it to `serpapi` with a "
                    f"`SERPAPI_KEY` to enable lookups."
                )
                return

            # Optional explicit store id as the last arg; else the first watched.
            stores = self.store_ids()
            store_id = ""
            if len(args) >= 2 and args[-1].isdigit() and args[-1] != item_id:
                store_id = args[-1]
            elif stores:
                store_id = stores[0]
            if not store_id:
                await ctx.reply(
                    f"Found item `{item_id}`, but no store is configured "
                    f"(`DEFAULT_STORE_ID` / `STORE_IDS` / `!addstore`)."
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

        @self.command(name="addstore")
        async def addstore(ctx: commands.Context, store_id: str = ""):
            """!addstore <store_id> — also watch this store for penny hits."""
            if not self._in_command_channel(ctx):
                return
            if not store_id.isdigit():
                await ctx.reply("Usage: `!addstore <store_id>` (digits only)")
                return
            added = self.state.add_store(store_id)
            await ctx.reply(
                f"{'Added' if added else 'Already watching'} store `{store_id}`. "
                f"Now watching: {', '.join(f'`{s}`' for s in self.store_ids())}"
            )

        @self.command(name="removestore")
        async def removestore(ctx: commands.Context, store_id: str = ""):
            """!removestore <store_id> — stop watching a runtime-added store."""
            if not self._in_command_channel(ctx):
                return
            if not store_id:
                await ctx.reply("Usage: `!removestore <store_id>`")
                return
            removed = self.state.remove_store(store_id)
            if not removed:
                await ctx.reply(
                    f"`{store_id}` isn't a runtime-added store. "
                    f"(Config stores from DEFAULT_STORE_ID/STORE_IDS can't be removed at runtime.)"
                )
                return
            await ctx.reply(
                f"Removed store `{store_id}`. Now watching: "
                f"{', '.join(f'`{s}`' for s in self.store_ids()) or '(none)'}"
            )

        @self.command(name="liststores")
        async def liststores(ctx: commands.Context):
            """!liststores — show the stores currently being watched."""
            if not self._in_command_channel(ctx):
                return
            stores = self.store_ids()
            await ctx.reply(
                "Watching: " + (", ".join(f"`{s}`" for s in stores) if stores else "(none configured)")
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
