"""Tests for the web dashboard routes (aiohttp in-process test server)."""
import asyncio

from aiohttp.test_utils import TestClient, TestServer
from aiohttp import web

from bot.dashboard import Dashboard
from bot.state import State


def _make_app(state, token=None, stores=("2667",)):
    dash = Dashboard(state, port=0, token=token, store_ids=lambda: list(stores))
    app = web.Application()
    app.add_routes([
        web.get("/", dash._index),
        web.get("/api/alerts", dash._api_alerts),
        web.get("/healthz", dash._healthz),
    ])
    return app


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_api_returns_alerts_and_stats(tmp_path):
    st = State(str(tmp_path / "t.sqlite3"))
    st.log_alert(kind="hit", sku="1004567890", store_id="2667", name="X",
                 price=0.01, quantity=3)

    async def go():
        async with TestClient(TestServer(_make_app(st))) as client:
            resp = await client.get("/api/alerts")
            assert resp.status == 200
            data = await resp.json()
            assert data["stats"]["hits"] == 1
            assert data["stats"]["stores_watched"] == 1
            assert data["alerts"][0]["sku"] == "1004567890"
            assert data["stores"] == ["2667"]

            health = await client.get("/healthz")
            assert (await health.json())["ok"] is True

            index = await client.get("/")
            assert index.status == 200
            assert "Penny Deal Alerts" in await index.text()

    _run(go())
    st.close()


def test_token_gates_access(tmp_path):
    st = State(str(tmp_path / "t.sqlite3"))

    async def go():
        async with TestClient(TestServer(_make_app(st, token="secret"))) as client:
            assert (await client.get("/api/alerts")).status == 401
            assert (await client.get("/api/alerts?token=wrong")).status == 401
            ok = await client.get("/api/alerts?token=secret")
            assert ok.status == 200
            # health is always open (no token needed)
            assert (await client.get("/healthz")).status == 200
            # token is injected into the page so its fetch() stays authorized
            page = await (await client.get("/?token=secret")).text()
            assert "secret" in page

    _run(go())
    st.close()
