"""Tests for the regex SKU extractor (Phase 1's core logic)."""
from bot import sku_extractor


def test_extracts_internet_id_from_url():
    text = "Check it: https://www.homedepot.com/p/Some-Cool-Drill/312345678 penny!"
    r = sku_extractor.extract(text)
    assert "312345678" in r.internet_ids
    assert r.looks_pennyish


def test_extracts_labelled_sku():
    r = sku_extractor.extract("Internet # 1004567890 is a penny at my store")
    assert "1004567890" in r.internet_ids


def test_extracts_model_number():
    r = sku_extractor.extract("Model# DCD771C2 is the one")
    assert "DCD771C2" in r.model_numbers


def test_bare_number_only_when_pennyish():
    # No penny hint -> bare number ignored (could be an order/phone number).
    assert not sku_extractor.extract("call me at 1234567890").has_any
    # With penny hint -> bare number surfaced.
    r = sku_extractor.extract("this 1004567890 rang up as a penny!")
    assert "1004567890" in r.internet_ids


def test_url_id_preferred_over_bare_fallback():
    text = "penny deal homedepot.com/p/x/999888777 and also 1112223334"
    r = sku_extractor.extract(text)
    # URL id present, so weak bare fallback is skipped.
    assert r.internet_ids == ["999888777"]


def test_dedupes():
    text = "penny 1004567890 ... again 1004567890"
    r = sku_extractor.extract("Internet # 1004567890 penny Internet # 1004567890")
    assert r.internet_ids == ["1004567890"]


def test_empty():
    assert not sku_extractor.extract("").has_any
    assert not sku_extractor.extract("just a normal post about drills").has_any
