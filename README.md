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
| **2** | Store inventory verification behind a swappable `check_item()` interface (SerpApi), `!check` command | 🟡 interface + SerpApi skeleton in place; wiring/verification pending real key + store id |
| **3** | Daily alert dedup, failure DMs, LLM SKU fallback, multi-store | 🟡 dedup + failure-DM hooks present; LLM fallback not started |

> **Important design rule (from the brief):** the inventory checker is the
> single biggest long-term reliability risk. It lives entirely behind
> `bot/inventory/` so the SerpApi provider can be hardened — or swapped for
> BigBox API / a custom scraper — without touching the Reddit pipeline or the
> Discord bot.

---

## Architecture

```
Reddit (PRAW)  ─►  SKU Extractor  ─►  Dedup/State  ─►  Discord alert
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
| `bot/reddit_watcher.py` | asyncpraw poller for new posts **and** comments |
| `bot/sku_extractor.py` | regex extraction of internet#/SKU/model#/product-URL ids |
| `bot/state.py` | SQLite: seen posts/comments + per-(SKU,store) daily alert dedup |
| `bot/discord_bot.py` | bot, poll loop, `!check` / `!ping` commands |
| `bot/alerts.py` | Discord embed formatting |
| `bot/inventory/` | the swappable Problem-B boundary (`base.py`, `serpapi_provider.py`) |
| `tests/` | unit tests for extractor, state, inventory value types |

---

## Setup (Phase 0)

### 1. Reddit app (free)
1. Go to <https://www.reddit.com/prefs/apps> → **create another app…**
2. Type: **script**. Redirect URI can be `http://localhost:8080`.
3. Copy the **client id** (under the app name) and **secret** into
   `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`.
4. Set `REDDIT_USER_AGENT` to something like
   `penny-deal-bot/0.1 by u/yourname`.

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

---

## Deploy on Railway

1. New project → **Deploy from GitHub repo** (this repo, branch
   `claude/stoic-sagan-q1ehxc`).
2. **Service type must be a worker, not a web service.** This bot does not
   listen on a port; a web service would be killed for "no port detected".
   The included `Procfile` declares `worker: python main.py`; `railway.json`
   sets the start command and an on-failure restart policy.
3. Add all env vars from `.env.example` under the service's **Variables**.
4. **Persist SQLite across redeploys:** Railway containers are ephemeral.
   Add a **Volume** mounted at e.g. `/data` and set `DB_PATH=/data/penny.sqlite3`.
   Without this, dedup state resets on every deploy (you'll get re-alerted).

---

## Commands

| Command | Effect |
|---------|--------|
| `!ping` | health check (`pong 🪙`) |
| `!check <SKU>` | look up a SKU at `DEFAULT_STORE_ID` (Phase 2; replies that checking is disabled until a provider + store id are configured) |
| `!check <homedepot.com product URL>` | same, parsing the item id out of the URL |

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

Follows the brief's Section 8 in order; Phase 1 was built and verified before
any Phase 2 inventory-gating logic. Phase 2 next steps: obtain a SerpApi key,
confirm the store number, verify the field mappings in
`serpapi_provider._parse` against a live response, then gate alerts on
`ItemStatus.is_penny_hit(threshold)`.
