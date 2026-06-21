"""Web dashboard of alert history (Phase 3 stretch).

Runs an aiohttp server *inside the bot's event loop* — no second process. It is
optional: the bot only starts it when DASHBOARD_PORT (or Railway's PORT) is set.

  * no port set  -> bot stays a portless Railway *worker* (the brief's default)
  * port set     -> deploy as a Railway *web service*; the dashboard is served
                    and the Reddit/Discord background work runs alongside it

Data comes straight from the same SQLite `State`. An optional DASHBOARD_TOKEN
gates access (passed as `?token=` or an `X-Dashboard-Token` header) since a
Railway web service is publicly reachable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiohttp import web

log = logging.getLogger(__name__)


class Dashboard:
    def __init__(self, state, *, port: int, token: str | None = None,
                 store_ids=None):
        self._state = state
        self._port = port
        self._token = token
        # Callable returning the live watched-store list (so the header reflects
        # runtime !addstore changes), or None.
        self._store_ids = store_ids
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.add_routes([
            web.get("/", self._index),
            web.get("/api/alerts", self._api_alerts),
            web.get("/healthz", self._healthz),
        ])
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host="0.0.0.0", port=self._port)
        await site.start()
        log.info("Dashboard listening on :%d%s", self._port,
                 " (token-protected)" if self._token else "")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    # ── auth ─────────────────────────────────────────────────────────────────
    def _authorized(self, request: web.Request) -> bool:
        if not self._token:
            return True
        supplied = (
            request.query.get("token")
            or request.headers.get("X-Dashboard-Token", "")
        )
        return supplied == self._token

    # ── routes ───────────────────────────────────────────────────────────────
    async def _healthz(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def _api_alerts(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            limit = int(request.query.get("limit", "100"))
        except ValueError:
            limit = 100
        stats = self._state.alert_stats()
        if self._store_ids is not None:
            stats["stores_watched"] = len(self._store_ids())
        return web.json_response({
            "stats": stats,
            "alerts": self._state.recent_alerts(limit),
            "stores": list(self._store_ids()) if self._store_ids else [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })

    async def _index(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.Response(status=401, text="Unauthorized — append ?token=…")
        # Forward the token so the page's fetch() calls stay authorized.
        token = request.query.get("token", "")
        return web.Response(text=_PAGE.replace("__TOKEN__", token),
                            content_type="text/html")


# Single self-contained page — no external assets, polls /api/alerts.
_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>🪙 Penny Deal Alerts</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:#0f1115; color:#e6e6e6; }
  header { padding:18px 24px; background:#161922; border-bottom:1px solid #262b36;
           display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  h1 { font-size:18px; margin:0; }
  .stats { display:flex; gap:18px; flex-wrap:wrap; margin-left:auto; }
  .stat { text-align:center; }
  .stat b { display:block; font-size:20px; color:#fff; }
  .stat span { font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:#8b93a7; }
  main { padding:18px 24px; }
  table { width:100%; border-collapse:collapse; }
  th,td { text-align:left; padding:8px 10px; border-bottom:1px solid #21262f; vertical-align:top; }
  th { font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:#8b93a7; }
  tr:hover td { background:#161922; }
  .tag { display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px; font-weight:600; }
  .hit { background:#143524; color:#4ade80; }
  .mention { background:#3a2f12; color:#fbbf24; }
  a { color:#60a5fa; text-decoration:none; } a:hover { text-decoration:underline; }
  .muted { color:#6b7280; } .price { font-variant-numeric:tabular-nums; }
  footer { padding:12px 24px; color:#6b7280; font-size:12px; }
  .empty { padding:40px; text-align:center; color:#6b7280; }
</style></head>
<body>
<header>
  <h1>🪙 Penny Deal Alerts</h1>
  <div class="stats" id="stats"></div>
</header>
<main>
  <div id="stores" class="muted" style="margin-bottom:12px"></div>
  <table id="tbl"><thead><tr>
    <th>When</th><th>Type</th><th>SKU</th><th>Item</th><th>Price</th>
    <th>Qty</th><th>Store</th><th>Source</th>
  </tr></thead><tbody id="rows"></tbody></table>
  <div id="empty" class="empty" hidden>No alerts logged yet.</div>
</main>
<footer>Auto-refreshes every 30s · <span id="ts"></span></footer>
<script>
const TOKEN = "__TOKEN__";
const q = TOKEN ? ("?token=" + encodeURIComponent(TOKEN)) : "";
const esc = s => (s==null?"":String(s)).replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}[c]));
const ago = ts => { const d = Math.floor(Date.now()/1000 - ts);
  if (d<60) return d+"s ago"; if (d<3600) return Math.floor(d/60)+"m ago";
  if (d<86400) return Math.floor(d/3600)+"h ago"; return Math.floor(d/86400)+"d ago"; };
const hdUrl = sku => "https://www.homedepot.com/p/" + encodeURIComponent(sku);

async function load() {
  let data;
  try { const r = await fetch("/api/alerts" + q); if (!r.ok) throw 0; data = await r.json(); }
  catch (e) { document.getElementById("ts").textContent = "fetch failed"; return; }
  const s = data.stats;
  document.getElementById("stats").innerHTML =
    `<div class=stat><b>${s.hits}</b><span>Penny hits</span></div>` +
    `<div class=stat><b>${s.mentions}</b><span>Mentions</span></div>` +
    `<div class=stat><b>${s.total}</b><span>Total</span></div>` +
    `<div class=stat><b>${s.stores_watched}</b><span>Stores</span></div>`;
  document.getElementById("stores").textContent =
    data.stores.length ? ("Watching stores: " + data.stores.join(", ")) : "No stores configured";
  const rows = data.alerts.map(a => {
    const tag = a.kind === "hit" ? "<span class='tag hit'>HIT</span>"
                                 : "<span class='tag mention'>mention</span>";
    const sku = a.sku ? `<a href="${hdUrl(a.sku)}" target=_blank>${esc(a.sku)}</a>` : "<span class=muted>—</span>";
    const price = a.price!=null ? `<span class=price>$${Number(a.price).toFixed(2)}</span>` : "<span class=muted>—</span>";
    const src = a.source_url ? `<a href="${esc(a.source_url)}" target=_blank>${esc(a.subreddit||"link")}</a>` : "<span class=muted>—</span>";
    return `<tr><td title="${new Date(a.ts*1000).toLocaleString()}">${ago(a.ts)}</td>`+
      `<td>${tag}</td><td>${sku}</td><td>${esc(a.name)||"<span class=muted>—</span>"}</td>`+
      `<td>${price}</td><td>${a.quantity!=null?a.quantity:"<span class=muted>—</span>"}</td>`+
      `<td>${esc(a.store_id)||"<span class=muted>—</span>"}</td><td>${src}</td></tr>`;
  }).join("");
  document.getElementById("rows").innerHTML = rows;
  document.getElementById("empty").hidden = data.alerts.length > 0;
  document.getElementById("tbl").hidden = data.alerts.length === 0;
  document.getElementById("ts").textContent = "updated " + new Date().toLocaleTimeString();
}
load(); setInterval(load, 30000);
</script>
</body></html>"""
