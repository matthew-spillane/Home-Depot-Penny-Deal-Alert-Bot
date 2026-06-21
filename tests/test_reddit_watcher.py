"""Tests for the watcher's regex-then-LLM extraction gating (no network)."""
import asyncio

from bot.reddit_watcher import RedditWatcher


def _run(text, llm_extract):
    return asyncio.run(RedditWatcher._extract(text, llm_extract))


def test_no_llm_call_when_regex_finds_id():
    calls = []

    async def llm(t):
        calls.append(t)
        return ["999999999"]

    # URL gives a strong id -> LLM must NOT be consulted.
    r = _run("penny! homedepot.com/p/x/312345678", llm)
    assert r.internet_ids == ["312345678"]
    assert calls == []


def test_no_llm_call_when_not_pennyish():
    calls = []

    async def llm(t):
        calls.append(t)
        return ["999999999"]

    # No penny hint and no id -> not relevant, don't waste an LLM call.
    r = _run("anyone know about this drill 1234567890?", llm)
    assert calls == []
    assert not r.internet_ids


def test_llm_fallback_recovers_id():
    async def llm(t):
        return ["1004567890"]

    # Pennyish but regex finds no id -> LLM fallback fills it in.
    r = _run("this rang up as a penny, it's the dewalt one, look it up", llm)
    assert r.internet_ids == ["1004567890"]
    assert r.looks_pennyish


def test_llm_fallback_empty_keeps_regex_result():
    async def llm(t):
        return []

    r = _run("penny deal somewhere here", llm)
    assert r.internet_ids == []


def test_no_llm_configured():
    r = _run("penny deal but no llm wired", None)
    assert r.internet_ids == []
