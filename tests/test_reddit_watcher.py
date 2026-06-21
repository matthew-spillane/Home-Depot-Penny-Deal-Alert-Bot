"""Tests for the Reddit watcher: public-JSON parsing and the regex-then-LLM
extraction gating (no network)."""
import asyncio

from bot.reddit_watcher import RedditWatcher
from bot.sku_extractor import ExtractionResult


def _run(text, llm_extract):
    return asyncio.run(RedditWatcher._extract(text, llm_extract))


# ── public-JSON payload parsing ──────────────────────────────────────────────
def test_children_pulls_data_dicts():
    payload = {"data": {"children": [
        {"kind": "t3", "data": {"name": "t3_abc", "title": "hi"}},
        {"kind": "t3", "data": {"name": "t3_def"}},
        "garbage",
    ]}}
    rows = RedditWatcher._children(payload)
    assert [r.get("name") for r in rows] == ["t3_abc", "t3_def"]


def test_children_handles_bad_payload():
    assert RedditWatcher._children(None) == []
    assert RedditWatcher._children({}) == []
    assert RedditWatcher._children({"data": {}}) == []


def test_mention_from_post():
    data = {
        "name": "t3_abc",
        "title": "penny deal!",
        "subreddit": "homedepot",
        "author": "someuser",
        "permalink": "/r/homedepot/comments/abc/penny_deal/",
    }
    m = RedditWatcher._mention_from_post(data, ExtractionResult(internet_ids=["312345678"]))
    assert m.reddit_id == "t3_abc"
    assert m.kind == "post"
    assert m.subreddit == "homedepot"
    assert m.author == "someuser"
    assert m.permalink == "https://www.reddit.com/r/homedepot/comments/abc/penny_deal/"


def test_mention_from_comment_snippet_and_deleted_author():
    data = {
        "name": "t1_xyz",
        "body": "line one\nline two with sku 1004567890",
        "subreddit": "homedepot",
        "author": "[deleted]",
        "permalink": "/r/homedepot/comments/abc/x/xyz/",
    }
    m = RedditWatcher._mention_from_comment(data, ExtractionResult())
    assert m.reddit_id == "t1_xyz"
    assert m.kind == "comment"
    assert m.author is None                      # [deleted] -> None
    assert "\n" not in m.title                   # newlines flattened for the snippet
    assert m.title.startswith("line one")


def test_poll_parses_dedupes_and_builds_mentions():
    w = RedditWatcher(user_agent="test-ua", subreddits=["homedepot", "pennydeals"])
    assert w.subreddit_str == "homedepot+pennydeals"

    posts = [{
        "name": "t3_seen", "title": "old penny post homedepot.com/p/x/111111111",
        "subreddit": "homedepot", "author": "a", "permalink": "/r/homedepot/p/1/",
    }, {
        "name": "t3_new", "title": "penny! homedepot.com/p/x/312345678",
        "subreddit": "homedepot", "author": "b", "permalink": "/r/homedepot/p/2/",
    }]
    comments = [{
        "name": "t1_new", "body": "this rang up a penny, internet # 1004567890",
        "subreddit": "homedepot", "author": "c", "permalink": "/r/homedepot/c/1/",
    }, {
        "name": "t1_noise", "body": "just chatting about drills",
        "subreddit": "homedepot", "author": "d", "permalink": "/r/homedepot/c/2/",
    }]

    async def fake_fetch(kind):
        return posts if kind == "new" else comments

    w._fetch_listing = fake_fetch  # stub the HTTP layer
    seen = {"t3_seen"}             # pretend we already alerted on the first post

    mentions = asyncio.run(w.poll(seen.__contains__))
    ids = {m.reddit_id for m in mentions}
    assert ids == {"t3_new", "t1_new"}   # seen post + irrelevant comment dropped
    by_id = {m.reddit_id: m for m in mentions}
    assert by_id["t3_new"].extraction.internet_ids == ["312345678"]
    assert by_id["t1_new"].extraction.internet_ids == ["1004567890"]


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
