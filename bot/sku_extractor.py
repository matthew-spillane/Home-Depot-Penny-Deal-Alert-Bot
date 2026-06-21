"""Extract Home Depot item identifiers from free-form Reddit text.

Phase 1 is regex-only (fast, free). The LLM fallback for messy posts is a
Phase 3 item — see README. We deliberately keep this conservative: it is fine
to miss some SKUs, and a little noise is acceptable, but we try to avoid
obvious false positives like prices and dates.

Home Depot exposes a few different identifiers in the wild:
  * Internet # / Store SKU — typically 9-10 digit numbers.
  * Model #              — alphanumeric, manufacturer-specific.
  * Product URLs         — homedepot.com/p/<slug>/<internet_id>  (most reliable).

The URL-embedded internet id is the strongest signal, so we surface those
separately and prefer them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# homedepot.com/p/Some-Product-Slug/123456789  (the trailing number is the
# "internet number" / product id used by HD's own APIs).
_URL_RE = re.compile(
    r"homedepot\.com/p/[^\s/]+/(\d{6,12})",
    re.IGNORECASE,
)

# Explicit call-outs: "SKU 1234567", "Internet # 123456789", "Model# ABC123".
_LABELLED_NUM_RE = re.compile(
    r"\b(?:internet\s*#?|sku\s*#?|item\s*#?|store\s*sku\s*#?)\s*[:#]?\s*(\d{6,12})\b",
    re.IGNORECASE,
)
_MODEL_RE = re.compile(
    r"\bmodel\s*#?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{3,19})\b",
    re.IGNORECASE,
)

# Bare 9-10 digit numbers (HD internet numbers are usually 9 digits). Used only
# as a weak fallback when the post otherwise smells like a penny deal.
_BARE_NUM_RE = re.compile(r"\b(\d{9,10})\b")

# Words that suggest a post is actually about a penny deal, used to gate the
# weak bare-number heuristic and to score relevance.
_PENNY_HINT_RE = re.compile(
    r"\b(penny|pennies|\$?0?\.0[1-3]\b|one cent|1\s*cent|clearance glitch)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractionResult:
    """SKUs found in a piece of text, plus relevance signal."""

    internet_ids: list[str] = field(default_factory=list)  # strongest signal
    model_numbers: list[str] = field(default_factory=list)
    looks_pennyish: bool = False

    @property
    def has_any(self) -> bool:
        return bool(self.internet_ids or self.model_numbers)

    @property
    def all_skus(self) -> list[str]:
        """Numeric identifiers worth checking against the store, de-duped."""
        return self.internet_ids


def _dedupe(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def extract(text: str) -> ExtractionResult:
    """Pull candidate HD identifiers out of a blob of text."""
    if not text:
        return ExtractionResult()

    looks_pennyish = bool(_PENNY_HINT_RE.search(text))

    internet_ids: list[str] = []
    internet_ids += _URL_RE.findall(text)
    internet_ids += _LABELLED_NUM_RE.findall(text)

    models = _MODEL_RE.findall(text)

    # Weak fallback: bare 9-10 digit numbers, but only if the post reads like a
    # penny deal and we did not already find a labelled/URL id (avoids pulling
    # in phone numbers, order numbers, etc. from unrelated posts).
    if looks_pennyish and not internet_ids:
        internet_ids += _BARE_NUM_RE.findall(text)

    return ExtractionResult(
        internet_ids=_dedupe(internet_ids),
        model_numbers=_dedupe(models),
        looks_pennyish=looks_pennyish,
    )
