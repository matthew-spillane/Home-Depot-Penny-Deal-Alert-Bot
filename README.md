# 🪙 Home Depot Penny Deal Alert Bot

Watches Reddit for Home Depot **penny deal** chatter (items that have glitched
down to $0.01–$0.03 in HD's register system), extracts the SKUs being
discussed, and pings a Discord channel. Phase 2 adds store-specific inventory
verification and an on-demand `!check <SKU>` command.

Built for North Shore MA. Python backend, single persistent process, deployed
on Railway as a **worker** service.

---

## Status — where this build is

This repo implements **Phase 1 end-to-end** plus the scaffolding the later
phases slot into. See [the build order](#build-phases) below.

| Phase | Scope | State |
|------|-------|-------|
| **1** | Reddit → Discord raw "possible penny deal mentioned" alerts, regex SKU extraction, SQLite dedup | ✅ implemented |
| **2** | Store inventory verification behind a swappable `check_item()` interface (SerpApi); poll loop alerts only on verified penny hits; `!check` command | ✅ implemented — pending a real SerpApi key + store id to validate field mappings against live data |
| **3** | Daily alert dedup, failure DMs, LLM SKU fallback, multi-store | ✅ implemented — daily dedup, inventory/poll failure DMs, Claude LLM fallback for messy posts, multi-store watching + `!addstore`/`!removestore`/`!liststores` |

### How verification gates alerts (Phase 2)

The bot has two modes, chosen automatically at startup:

- **Phase 2 (verified):** active when `INVENTORY_PROVIDER` is a real provider
  (e.g. `serpapi`) **and** `DEFAULT_STORE_ID` is set. Each extracted SKU is
  looked up at your store; the bot only posts a 🟢 alert when it's a real
  **penny hit** — `price ≤ PENNY_PRICE_THRESHOLD` **and** in stock. Per-(SKU,
  store) daily dedup keeps the same hit from re-pinging.
- **Phase 1 (fallback):** if a provider or store isn't configured, the bot
  keeps posting unverified 🟡 "possible penny deal mentioned" alerts, so it's
  still useful before Phase 2 config is in place.

If inventory lookups start failing repeatedly (HTTP errors / timeouts — *not*
"item not found"), the bot DMs `DISCORD_OWNER_ID` so a silently-broken provider
gets noticed fast.

### LLM SKU fallback (Phase 3)

Regex handles clean posts for free. When a post *reads* like a penny deal but
regex finds no item id, the bot can ask Claude to extract the SKU. It's strictly
a fallback: regex first, and the LLM is only called when a post is pennyish
**and** id-less, capped at `LLM_MAX_CALLS_PER_POLL` calls per cycle to bound
cost. Enabled automatically when `ANTHROPIC_API_KEY` is set; defaults to
`claude-opus-4-8` — set `LLM_MODEL=claude-haiku-4-5` if you'd rather trade a
little accuracy for lower cost on this high-volume path. The model returns a
structured `{item_ids, is_penny_deal}` result, and ids are re-validated
(6–12 digits) before they're trusted.

### Multi-store (Phase 3)

Watch more than one store. Configure a base set via `DEFAULT_STORE_ID` +
`STORE_IDS` (comma-separated), and add/remove stores live with `!addstore` /
`!removestore` (runtime stores persist in SQLite). Each extracted SKU is checked
against every watched store; penny-hit dedup is per-`(SKU, store)` so each store
can alert independently. Handy once the Peabody shop is in play.

### Web dashboard (Phase 3 stretch)

A self-contained web page showing alert history (penny hits + mentions), live
stats, and the stores being watched — served by an aiohttp server **inside the
bot's own event loop**, so there's still just one process.

- **Off by default.** Leave `DASHBOARD_PORT` unset and the bot stays a portless
  Railway *worker* exactly as before.
- **To enable:** set `DASHBOARD_PORT` (locally, e.g. `8080` → open
  `http://localhost:8080`). On a Railway **web** service, `PORT` is injected
  automatically, so just deploy as a web service and leave `DASHBOARD_PORT`
  blank — the dashboard uses `PORT`.
- **Protect it:** a Railway web service is public, so set `DASHBOARD_TOKEN`;
  the page then requires `?token=…`. `/healthz` stays open for health checks.

Data is read straight from the same SQLite DB (`alerts_log` table), and the page
auto-refreshes every 30s via a small `/api/alerts` JSON endpoint.

> **Important design rule (from the brief):** the inventory checker is the
> single biggest long-term reliability risk. It lives entirely behind
> `bot/inventory/` so the SerpApi provider can be hardened — or swapped for
> BigBox API / a custom scraper — without touching the Reddit pipeline or the
> Discord bot.

---

## Architecture

```
Reddit JSON    ─►  SKU Extractor  ─►  Dedup/State  ─►  Discord alert
  poll loop         (regex)           (SQLite)          (embed)
                                          │
                            Phase 2:  Inventory Checker  ◄── !check <SKU>
                                      (check_item interface → SerpApi)
```

Everything runs in **one process / one event loop**: the Discord bot owns the
loop and runs the Reddit poll as a `discord.ext.tasks` background task. One
Railway worker service, no exposed port.

| File | Role |
|------|------|
| `main.py` | entrypoint; builds `Config`, starts the bot |
| `bot/config_types.py` | env-var config (`Config.load()`) |
| `bot/reddit_watcher.py` | public-JSON (aiohttp) poller for new posts **and** comments |
| `bot/sku_extractor.py` | regex extraction of internet#/SKU/model#/product-URL ids |
| `bot/state.py` | SQLite: seen posts/comments + per-(SKU,store) daily alert dedup |
| `bot/discord_bot.py` | bot, poll loop, `!check` / `!ping` commands |
| `bot/alerts.py` | Discord embed formatting |
| `bot/inventory/` | the swappable Problem-B boundary (`base.py`, `serpapi_provider.py`) |
| `bot/llm_extractor.py` | Phase 3 Claude fallback for messy posts |
| `bot/dashboard.py` | optional aiohttp web dashboard of alert history |
| `tests/` | unit tests for extractor, state, inventory, watcher, dashboard |

---

## Setup (Phase 0)

### 1. Reddit — nothing to register

The watcher reads Reddit's **public, unauthenticated `.json` feeds** over plain
HTTP, so there is **no Reddit account or app registration** (Reddit's script-app
flow is now gated behind a moderation-use-case review that doesn't fit this
project — and read-only public JSON doesn't need it). The only requirement is a
descriptive `REDDIT_USER_AGENT` header — Reddit blocks generic/empty agents — and
a sensible default is used if you leave it unset.

### 2. Discord bot
1. <https://discord.com/developers/applications> → **New Application** → **Bot**.
2. Copy the **token** → `DISCORD_BOT_TOKEN`.
3. Under **Bot → Privileged Gateway Intents**, enable **Message Content
   Intent** (required to read `!check` arguments).
4. Invite the bot: **OAuth2 → URL Generator**, scopes `bot`, permissions
   *Send Messages* + *Embed Links* + *Read Message History*. Open the URL and
   add it to your server.
5. Enable **Developer Mode** in Discord (Settings → Advanced), right-click your
   alerts channel → **Copy Channel ID** → `DISCORD_ALERTS_CHANNEL_ID`.
6. (Optional) right-click yourself → Copy User ID → `DISCORD_OWNER_ID` for
   failure DMs.

### 3. Your Home Depot store number (needed for Phase 2 only)
Set your store on homedepot.com; the store number appears in the URL / store
locator. North Shore MA candidates to verify: **Lynn**, **Saugus**,
**Danvers/Peabody**. Put it in `DEFAULT_STORE_ID`. Not required for Phase 1.

### 4. SerpApi (Phase 2 only)
Sign up at <https://serpapi.com/> (free tier to prototype), put the key in
`SERPAPI_KEY`, and set `INVENTORY_PROVIDER=serpapi`.

---

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in the values
python main.py
```

You should see `Logged in as …` and `Watching r/homedepot+pennydeals every 90s`.
Post a test message containing a HD product URL in a watched subreddit (or wait
for real chatter) and the bot will drop a 🟡 embed in your alerts channel.

### Tests
```bash
pip install pytest
python -m pytest -q
```
Tests cover the pure logic (SKU regex, SQLite dedup, penny-hit rule, provider
factory) — no network or secrets required.

### Validating the SerpApi mapping (do this before trusting Phase 2)

The SerpApi field paths in `serpapi_provider._parse` are best-effort until
checked against a live response. Use the probe to compare the raw JSON against
what the bot's penny-hit gate actually sees:

```bash
export SERPAPI_KEY=...                       # or put it in .env
python -m scripts.probe_serpapi 312345678 --store 2667
```

It prints the **raw** SerpApi JSON, then the **mapped** `ItemStatus`, and warns
if `price`/`quantity` failed to map. If a value is wrong, edit `_parse` and
re-run until they line up. The engine name and request params are also guesses
— override them without code changes via `--engine` and `--param K=V`:

```bash
python -m scripts.probe_serpapi 312345678 --engine home_depot_product \
    --param delivery_zip=01902 --raw-only
```


---

## Deploy on Railway

1. New project → **Deploy from GitHub repo** (this repo, branch
   `claude/stoic-sagan-q1ehxc`).
2. **Service type:** by default this bot does not listen on a port, so run it as
   a **worker** (a web service would be killed for "no port detected"). The
   included `Procfile` declares `worker: python main.py`; `railway.json` sets the
   start command and an on-failure restart policy.
   *If you want the web dashboard*, run it instead as a **web** service and set
   `DASHBOARD_TOKEN` — Railway injects `PORT`, the bot serves the dashboard on
   it, and the Reddit/Discord background work runs alongside in the same process.
3. Add all env vars from `.env.example` under the service's **Variables**.
4. **Persist SQLite across redeploys:** Railway containers are ephemeral.
   Add a **Volume** mounted at e.g. `/data` and set `DB_PATH=/data/penny.sqlite3`.
   Without this, dedup state resets on every deploy (you'll get re-alerted).

---

## Commands

| Command | Effect |
|---------|--------|
| `!ping` | health check (`pong 🪙`) |
| `!check <SKU> [store_id]` | look up a SKU at a watched store (defaults to the first; optional explicit store id). Replies that checking is disabled until a provider + store are configured |
| `!check <homedepot.com product URL>` | same, parsing the item id out of the URL |
| `!addstore <store_id>` | also watch this store for penny hits (persists across restarts) |
| `!removestore <store_id>` | stop watching a runtime-added store |
| `!liststores` | show the stores currently being watched |

---

## Known limits (read before trusting it)

- **Unofficial HD data.** Phase 2 relies on a third-party wrapper of HD's
  internal API; it can rate-limit or break without notice. The provider is
  isolated behind `check_item()` and the poll loop DMs the owner after
  repeated failures so you find out fast.
- **Reddit signal is noisy.** Expect some false positives (people *asking* if
  something is a penny item). v1 favors recall over precision; perfect
  filtering is a v2 problem.
- **A register showing $0.01 isn't a guarantee** — penny items usually need an
  associate to honor them at checkout. This bot surfaces candidates; it can't
  promise a cashier rings it up.

## Build phases

Follows the brief's Section 8 in order. Phases 1–3 are implemented: Reddit→Discord
alerts, store verification, daily dedup + failure DMs, the Claude LLM SKU
fallback, and multi-store watching.

**Remaining to fully trust Phase 2's price gate:** obtain a SerpApi key, confirm
your store number, then verify the field mappings in `serpapi_provider._parse`
against a real response — the engine name (`home_depot_product`), the
`product_id`/`store_id` params, and the price/quantity/aisle paths are
best-effort guesses and **must** be checked against live JSON. Use
`python -m scripts.probe_serpapi <SKU> --store <id>` (see above), then `!check`.

Stretch items from the brief still open: a custom HD scraper to cut API cost,
and Lowe's support. (The web dashboard stretch goal is implemented — see above.)
