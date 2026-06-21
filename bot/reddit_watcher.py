"""Reddit watcher — Problem A (is anyone talking about a penny deal?).

Uses asyncpraw (the async build of PRAW from the same maintainers) so it shares
the Discord bot's event loop cleanly rather than blocking it. Read-only,
ToS-compliant, well within free-tier rate limits at a 60-120s poll.

Polls BOTH new submissions and new comments per subreddit, because penny SKUs
are frequently dropped in comments on a megathread rather than as top-level
posts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import asyncpraw

from . import sku_extractor
from .sku_extractor import ExtractionResult

log = logging.getLogger(__name__)


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
        client_id: str,
        client_secret: str,
        user_agent: str,
        subreddits: list[str],
        fetch_limit: int = 50,
    ):
        self._subreddits = subreddits
        self._fetch_limit = fetch_limit
        self._reddit = asyncpraw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
            check_for_async=False,
        )
        self._reddit.read_only = True

    @property
    def subreddit_str(self) -> str:
        return "+".join(self._subreddits)

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
            subreddit = await self._reddit.subreddit(self.subreddit_str)

            async for submission in subreddit.new(limit=self._fetch_limit):
                rid = submission.fullname
                if is_seen(rid):
                    continue
                text = f"{submission.title}\n{submission.selftext or ''}"
                extraction = await self._extract(text, llm_extract)
                if self._is_relevant(extraction):
                    mentions.append(self._mention_from_submission(submission, extraction))

            async for comment in subreddit.comments(limit=self._fetch_limit):
                rid = comment.fullname
                if is_seen(rid):
                    continue
                extraction = await self._extract(comment.body or "", llm_extract)
                if self._is_relevant(extraction):
                    mentions.append(self._mention_from_comment(comment, extraction))

        except Exception:
            log.exception("Reddit poll failed")
            raise

        return mentions

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
    def _mention_from_submission(submission, extraction: ExtractionResult) -> Mention:
        return Mention(
            reddit_id=submission.fullname,
            kind="post",
            subreddit=str(submission.subreddit),
            author=str(submission.author) if submission.author else None,
            title=submission.title,
            permalink=f"https://www.reddit.com{submission.permalink}",
            extraction=extraction,
        )

    @staticmethod
    def _mention_from_comment(comment, extraction: ExtractionResult) -> Mention:
        body = (comment.body or "").strip().replace("\n", " ")
        snippet = body[:140] + ("…" if len(body) > 140 else "")
        return Mention(
            reddit_id=comment.fullname,
            kind="comment",
            subreddit=str(comment.subreddit),
            author=str(comment.author) if comment.author else None,
            title=snippet or "(comment)",
            permalink=f"https://www.reddit.com{comment.permalink}",
            extraction=extraction,
        )

    async def close(self) -> None:
        await self._reddit.close()
