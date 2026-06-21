"""LLM fallback for SKU extraction (Phase 3).

The regex extractor in `sku_extractor` handles clean posts for free. Messy ones
— "rang up as a penny, it's the dewalt blower, look up 100-456-7890" — often
carry a real Home Depot item number that the regex misses. This module asks
Claude to pull item ids out of such text.

Per the build doc: regex first (fast/free), LLM only when regex finds nothing
AND the post smells like a penny deal. To bound cost, the watcher caps how many
LLM calls happen per poll cycle (`reset_budget()` / `budget_remaining`).

Uses the official Anthropic async SDK so it shares the bot's event loop, with
structured outputs so the model must return a typed `{item_ids, is_penny_deal}`
object rather than free-form text we'd have to parse loosely.
"""
from __future__ import annotations

import json
import logging
import re

from anthropic import AsyncAnthropic

log = logging.getLogger(__name__)

# Validate model output before trusting it: HD internet/item numbers are 6-12
# digit strings. This guards against the model echoing prices, years, etc.
_ID_RE = re.compile(r"^\d{6,12}$")


def validate_item_ids(raw_ids) -> list[str]:
    """Keep only plausible HD item ids (6-12 digits), de-duped, separators
    stripped. Guards against the model echoing prices/years/etc."""
    ids: list[str] = []
    for raw_id in raw_ids or []:
        cleaned = re.sub(r"\D", "", str(raw_id))
        if _ID_RE.match(cleaned) and cleaned not in ids:
            ids.append(cleaned)
    return ids

_SYSTEM = (
    "You extract Home Depot product identifiers from informal Reddit text about "
    "'penny deals' (items that have glitched to $0.01-$0.03). Return the Home "
    "Depot Internet/Item numbers (the 9-10 digit product ids used on "
    "homedepot.com/p/...) that the post is claiming or asking about. Strip any "
    "separators so each id is digits only. Do NOT return prices, years, phone "
    "numbers, order numbers, model numbers, or store numbers. If there is no "
    "usable item id, return an empty list."
)

_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "item_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Home Depot internet/item numbers, digits only.",
            },
            "is_penny_deal": {
                "type": "boolean",
                "description": "True if the post is actually about a penny deal.",
            },
        },
        "required": ["item_ids", "is_penny_deal"],
        "additionalProperties": False,
    },
}


class LLMExtractor:
    def __init__(self, *, api_key: str, model: str, max_calls_per_poll: int):
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_calls = max_calls_per_poll
        self._budget = max_calls_per_poll

    def reset_budget(self) -> None:
        """Call once at the start of each poll cycle to refill the call budget."""
        self._budget = self._max_calls

    @property
    def budget_remaining(self) -> int:
        return self._budget

    async def extract_item_ids(self, text: str) -> list[str]:
        """Return validated HD item ids found in `text`, or [] (never raises)."""
        if self._budget <= 0 or not text.strip():
            return []
        self._budget -= 1
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=512,
                system=_SYSTEM,
                # Cheap, deterministic extraction — keep effort low to control
                # cost/latency on this high-volume fallback path.
                output_config={"format": _FORMAT, "effort": "low"},
                messages=[{"role": "user", "content": text[:6000]}],
            )
        except Exception:
            log.exception("LLM extraction call failed")
            return []

        if resp.stop_reason == "refusal":
            log.warning("LLM extraction refused: %s", getattr(resp, "stop_details", None))
            return []

        raw = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            log.warning("LLM extraction returned non-JSON: %r", raw[:200])
            return []

        if not data.get("is_penny_deal"):
            return []
        return validate_item_ids(data.get("item_ids", []))

    async def close(self) -> None:
        await self._client.close()
