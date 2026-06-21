"""Reddit watcher — Problem A (is anyone talking about a penny deal?).

Reads Reddit's **public, unauthenticated JSON feeds** over plain HTTP — no app
registration, no OAuth. Reddit's script-app registration is now gated behind a
moderation-use-case review that doesn't fit this project, and read-only public
JSON doesn't need it anyway.

  * new posts:    https://www.reddit.com/r/<subs>/new.json
  * new comments: https://www.reddit.com/r/<subs>/comments.json

(`<subs>` can be a `a+b+c` multireddit, so one request covers all watched subs.)

We poll BOTH endpoints because penny SKUs are frequently dropped in comments on
a megathread rather than as top-level posts. A descriptive User-Agent is
mandatory — Reddit blocks requests without one even for public JSON — and we
keep the poll interval conservative (60-120s, set by the caller) with backoff on
429/5xx to stay well clear of rate limits.

This module is isolated behind `poll()` / `Mention` / `close()`, so swapping the
data source touched nothing else in the bot.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace

import aiohttp

from . import sku_extractor
from .sku_extractor import ExtractionResult

log = logging.getLogger(__name__)

_BASE = "https://www.reddit.com"


@dataclass(frozen=True)
class Mention:
    """A single Reddit post/comment that mentions a possible penny deal."""

    reddit_id: str          # unique id (e.g. "t3_abc" / "t1_xyz")
    kind: str               # "post" or "comment"
    subreddit: str
    author: str | None
    title: str              # post title, or first line of comment
    permalink: str          # full https URL
    extraction: ExtractionResult


class RedditWatcher:
    def __init__(
        self,
        *,
        user_agent: str,
        subreddits: list[str],
        fetch_limit: int = 50,
        request_timeout: float = 20.0,
        max_retries: int = 3,
    ):
        self._subreddits = subreddits
        self._fetch_limit = fetch_limit
        self._user_agent = user_agent
        self._max_retries = max_retries
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._session: aiohttp.ClientSession | None = None

    @property
    def subreddit_str(self) -> str:
        return "+".join(self._subreddits)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # A descriptive UA is required — Reddit 429/403s default agents.
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": self._user_agent}
            )
        return self._session

    async def poll(self, is_seen, llm_extract=None) -> list[Mention]:
        """Scan new posts and comments; return mentions not yet seen.

        `is_seen(reddit_id) -> bool` lets the caller dedupe against persistent
        state. We do NOT mark items seen here — the caller does that after it
        has successfully handled the mention, so a crash mid-handling doesn't
        silently drop an alert.

        `llm_extract(text) -> list[str]` is the optional Phase 3 LLM fallback,
        called only when regex finds no id but the post smells pennyish.
        """
        mentions: list[Mention] = []
        try:
            for data in await self._fetch_listing("new"):
                rid = data.get("name", "")
                if not rid or is_seen(rid):
                    continue
                text = f"{data.get('title', '')}\n{data.get('selftext') or ''}"
                extraction = await self._extract(text, llm_extract)
                if self._is_relevant(extraction):
                    mentions.append(self._mention_from_post(data, extraction))

            for data in await self._fetch_listing("comments"):
                rid = data.get("name", "")
                if not rid or is_seen(rid):
                    continue
                extraction = await self._extract(data.get("body") or "", llm_extract)
                if self._is_relevant(extraction):
                    mentions.append(self._mention_from_comment(data, extraction))

        except Exception:
            log.exception("Reddit poll failed")
            raise

        return mentions

    # ── HTTP ─────────────────────────────────────────────────────────────────
    async def _fetch_listing(self, kind: str) -> list[dict]:
        """Fetch one listing endpoint ('new' or 'comments') and return the
        `data` dict of each child item."""
        url = f"{_BASE}/r/{self.subreddit_str}/{kind}.json"
        params = {"limit": self._fetch_limit, "raw_json": 1}
        payload = await self._get_json(url, params)
        return self._children(payload)

    @staticmethod
    def _children(payload) -> list[dict]:
        """Pull the per-item `data` dicts out of a Reddit listing payload."""
        if not isinstance(payload, dict):
            return []
        children = payload.get("data", {}).get("children", [])
        return [c.get("data", {}) for c in children if isinstance(c, dict)]

    async def _get_json(self, url: str, params: dict):
        """GET with retry/backoff on 429 and 5xx (honoring Retry-After)."""
        backoff = 2.0
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                session = await self._get_session()
                async with session.get(url, params=params, timeout=self._timeout) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    if resp.status == 429 or resp.status >= 500:
                        retry_after = resp.headers.get("Retry-After")
                        delay = float(retry_after) if retry_after else backoff
                        log.warning("Reddit HTTP %d on %s; backing off %.0fs (attempt %d/%d)",
                                    resp.status, url, delay, attempt, self._max_retries)
                        await asyncio.sleep(delay)
                        backoff *= 2
                        continue
                    # 403/404 etc. — not retryable (often a bad User-Agent).
                    body = (await resp.text())[:200]
                    raise RuntimeError(f"Reddit HTTP {resp.status} for {url}: {body}")
            except aiohttp.ClientError as exc:  # network / timeout
                last_exc = exc
                log.warning("Reddit request error on %s: %s (attempt %d/%d)",
                            url, exc, attempt, self._max_retries)
                await asyncio.sleep(backoff)
                backoff *= 2
        raise RuntimeError(
            f"Reddit fetch failed after {self._max_retries} attempts: {url}"
        ) from last_exc

    # ── parsing ──────────────────────────────────────────────────────────────
    @staticmethod
    async def _extract(text: str, llm_extract) -> ExtractionResult:
        """Regex first; LLM fallback only when regex found no id but the post
        reads pennyish (cheap/free path handles the common case)."""
        extraction = sku_extractor.extract(text)
        if extraction.internet_ids or not extraction.looks_pennyish or llm_extract is None:
            return extraction
        llm_ids = await llm_extract(text)
        if not llm_ids:
            return extraction
        log.info("LLM fallback recovered %d id(s) from a pennyish post", len(llm_ids))
        return replace(extraction, internet_ids=llm_ids)

    @staticmethod
    def _is_relevant(extraction: ExtractionResult) -> bool:
        # Phase 1 signal: we found at least one SKU AND the text reads pennyish,
        # OR we found a strong URL/labelled id (worth surfacing regardless).
        return extraction.has_any and (
            extraction.looks_pennyish or bool(extraction.internet_ids)
        )

    @staticmethod
    def _author(data: dict) -> str | None:
        author = data.get("author")
        return author if author and author != "[deleted]" else None

    @classmethod
    def _mention_from_post(cls, data: dict, extraction: ExtractionResult) -> Mention:
        return Mention(
            reddit_id=data.get("name", ""),
            kind="post",
            subreddit=str(data.get("subreddit", "")),
            author=cls._author(data),
            title=data.get("title", ""),
            permalink=f"{_BASE}{data.get('permalink', '')}",
            extraction=extraction,
        )

    @classmethod
    def _mention_from_comment(cls, data: dict, extraction: ExtractionResult) -> Mention:
        body = (data.get("body") or "").strip().replace("\n", " ")
        snippet = body[:140] + ("…" if len(body) > 140 else "")
        return Mention(
            reddit_id=data.get("name", ""),
            kind="comment",
            subreddit=str(data.get("subreddit", "")),
            author=cls._author(data),
            title=snippet or "(comment)",
            permalink=f"{_BASE}{data.get('permalink', '')}",
            extraction=extraction,
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
