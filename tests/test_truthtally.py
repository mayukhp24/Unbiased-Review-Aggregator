"""Unit tests for TruthTally's pure (non-browser) logic.

These cover the deterministic helpers extracted from the scraping and
analysis pipeline: URL normalization, ASIN extraction, candidate-URL
building, the sentiment interpretation threshold, bot-page detection,
and review parsing against a synthetic Amazon-shaped HTML fixture.

The Selenium/LLM paths are not unit-tested here (they depend on a live
browser and external services); they are covered by manual/exploratory
and system testing, as documented in the testing report.
"""
import os

import pytest

import truthtally_core as core

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample_reviews.html")


# --- normalize_url: equivalence partitions for URL input --------------------
@pytest.mark.parametrize("raw, expected", [
    ("https://www.amazon.in/dp/B0DQ8MGRNX", "https://www.amazon.in/dp/B0DQ8MGRNX"),
    ("http://www.amazon.in/dp/B0DQ8MGRNX", "http://www.amazon.in/dp/B0DQ8MGRNX"),
    ("www.amazon.in/dp/B0DQ8MGRNX", "https://www.amazon.in/dp/B0DQ8MGRNX"),
    ("amazon.in/dp/B0DQ8MGRNX", "https://amazon.in/dp/B0DQ8MGRNX"),
    ("   amazon.in/dp/B0DQ8MGRNX   ", "https://amazon.in/dp/B0DQ8MGRNX"),
    ("", ""),
])
def test_normalize_url(raw, expected):
    assert core.normalize_url(raw) == expected


# --- extract_asin: several Amazon URL shapes, plus the no-ASIN case ----------
@pytest.mark.parametrize("url, expected", [
    ("https://www.amazon.in/dp/B0DQ8MGRNX?th=1", "B0DQ8MGRNX"),
    ("https://www.amazon.com/gp/product/B08N5WRWNW", "B08N5WRWNW"),
    ("https://www.amazon.in/product-reviews/B0DPS7FIAB/", "B0DPS7FIAB"),
    ("https://www.amazon.in/OnePlus-13-256GB/dp/B0DPS7FIAB", "B0DPS7FIAB"),
    ("https://www.amazon.in/some-page-without-an-asin", None),
])
def test_extract_asin(url, expected):
    assert core.extract_asin(url) == expected


# --- build_candidate_urls: product page first, then reviews page + retry -----
def test_build_candidate_urls_with_asin():
    urls = core.build_candidate_urls("https://www.amazon.in/dp/B0DQ8MGRNX")
    assert urls[0] == "https://www.amazon.in/dp/B0DQ8MGRNX"
    assert len(urls) == 3  # product page + reviews page + one retry
    assert urls[1] == urls[2]
    assert "/product-reviews/B0DQ8MGRNX/" in urls[1]


def test_build_candidate_urls_without_asin():
    urls = core.build_candidate_urls("https://www.amazon.in/search")
    assert urls == ["https://www.amazon.in/search"]  # no reviews-page fallback


# --- interpret_sentiment: boundary value analysis around the +/-0.05 band ----
@pytest.mark.parametrize("compound, label", [
    (-0.06, "Overall Negative"),   # just below the negative boundary
    (-0.05, "Overall Neutral"),    # exactly at boundary -> dead-zone
    (0.0, "Overall Neutral"),
    (0.05, "Overall Neutral"),     # exactly at boundary -> dead-zone
    (0.06, "Overall Positive"),    # just above the positive boundary
    (0.9, "Overall Positive"),
    (-0.9, "Overall Negative"),
])
def test_interpret_sentiment(compound, label):
    assert core.interpret_sentiment(compound) == label


# --- is_bot_blocked: CAPTCHA/robot markers vs a clean page -------------------
def test_is_bot_blocked_true():
    html = "<html><body>Robot Check. Type the characters you see.</body></html>"
    assert core.is_bot_blocked(html) is True


def test_is_bot_blocked_false():
    html = "<html><body>Great phone with a lovely display.</body></html>"
    assert core.is_bot_blocked(html) is False


# --- parse_reviews: extraction + teaser-noise stripping on the fixture -------
def test_parse_reviews_extracts_all_blocks():
    with open(FIXTURE, encoding="utf-8") as f:
        html = f.read()
    reviews, block_count, sample_structure = core.parse_reviews(html)
    assert block_count == 3
    assert len(reviews) == 3
    assert sample_structure == ""  # no debug structure when extraction succeeds


def test_parse_reviews_strips_accessibility_teaser_noise():
    with open(FIXTURE, encoding="utf-8") as f:
        html = f.read()
    reviews, _, _ = core.parse_reviews(html)
    for review in reviews:
        assert "double tap to read" not in review
        assert "Brief content visible" not in review
        assert "Full content visible" not in review
    assert reviews[0].startswith("Excellent battery life")


def test_parse_reviews_empty_on_non_review_page():
    reviews, block_count, _ = core.parse_reviews("<html><body>No reviews here.</body></html>")
    assert reviews == []
    assert block_count == 0
