"""Tests for the LLM fallback's id-validation (pure logic, no API calls)."""
from bot.llm_extractor import validate_item_ids


def test_keeps_valid_ids_and_strips_separators():
    assert validate_item_ids(["312345678", "100-456-7890"]) == ["312345678", "1004567890"]


def test_rejects_too_short_or_too_long():
    # 5 digits (too short) and 13 digits (too long) are dropped.
    assert validate_item_ids(["12345", "1234567890123"]) == []


def test_dedupes_and_handles_non_strings():
    assert validate_item_ids(["312345678", "312345678", 100456789]) == ["312345678", "100456789"]


def test_empty_and_none():
    assert validate_item_ids([]) == []
    assert validate_item_ids(None) == []
    assert validate_item_ids(["", "abc", "$0.01"]) == []
