"""Pure, side-effect-free logic for TruthTally.

This module holds the deterministic business logic — URL handling, ASIN
extraction, sentiment thresholds, bot-page detection, and review parsing —
separated from the Flask/Selenium/Gemini IO layer in app.py.

Keeping it dependency-light (only BeautifulSoup) makes it fast and offline
to unit-test, without standing up a browser or external services.
"""
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

# --- Review-scraping selectors / constants ---
REVIEW_BLOCK_SELECTOR = (
    'div[data-hook="review"], li[data-hook="review"], '
    'div[id^="customer_review"], div[id*="review-card"]'
)
REVIEW_BODY_SELECTOR = (
    '[data-hook="reviewText"], '
    'span[data-hook="review-body"], '
    'div[data-hook="review-collapsed"], '
    'span.review-text-content, '
    '.review-text-content'
)
# Amazon injects accessibility "teaser" text into the review body; strip it.
NOISE_PHRASES = (
    "Brief content visible, double tap to read full content.",
    "Full content visible, double tap to read brief content.",
)
BOT_MARKERS = (
    "api-services-support@amazon",
    "type the characters you see",
    "enter the characters you see",
    "robot check",
    "to discuss automated access",
    "/errors/validatecaptcha",
)


def normalize_url(raw_url):
    """Trim the URL and prepend https:// when it has no scheme.

    Chrome's driver.get() rejects a scheme-less URL with an "invalid
    argument" error, and users often paste a bare domain.
    """
    url = (raw_url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def extract_asin(url):
    """Return the 10-character Amazon ASIN from a product URL, or None."""
    match = re.search(r'/(?:dp|gp/product|product|product-reviews)/([A-Z0-9]{10})', url)
    return match.group(1) if match else None


def build_candidate_urls(url):
    """Ordered list of URLs to try for one request.

    The inline product page sometimes omits reviews for a headless /
    datacenter browser, so also try Amazon's dedicated reviews page
    (more review-dense) and retry it once, since failures are often
    intermittent.
    """
    candidate_urls = [url]
    asin = extract_asin(url)
    if asin:
        netloc = urlparse(url).netloc or "www.amazon.com"
        reviews_url = (
            f"https://{netloc}/product-reviews/{asin}/"
            "?sortBy=recent&reviewerType=all_reviews&pageNumber=1"
        )
        candidate_urls += [reviews_url, reviews_url]  # try, then one retry
    return candidate_urls


def interpret_sentiment(compound):
    """Map an averaged VADER compound score to a label.

    Uses VADER's recommended +/-0.05 dead-zone so weakly-signed reviews
    are treated as Neutral rather than forced Positive/Negative.
    """
    if compound > 0.05:
        return "Overall Positive"
    if compound < -0.05:
        return "Overall Negative"
    return "Overall Neutral"


def is_bot_blocked(page_source):
    """True if the page HTML looks like an Amazon bot / CAPTCHA page."""
    lowered = page_source.lower()
    return any(marker in lowered for marker in BOT_MARKERS)


def parse_reviews(page_source):
    """Extract clean review texts from page HTML.

    Returns (reviews_list, block_count, sample_structure). The teaser
    accessibility divs and their "Brief/Full content visible..." text
    are stripped so downstream sentiment and AI summaries get clean text.
    """
    soup = BeautifulSoup(page_source, 'html.parser')
    review_elements = soup.select(REVIEW_BLOCK_SELECTOR)
    reviews_list = []
    for item in review_elements:
        body_element = item.select_one(REVIEW_BODY_SELECTOR)
        review_body = ""
        if body_element:
            # Drop the collapsed/expanded accessibility teaser divs first.
            for junk in body_element.select(
                '.a-teaser-describedby-collapsed, .a-teaser-describedby-expanded'
            ):
                junk.decompose()
            review_body = body_element.get_text(" ", strip=True)
            for noise in NOISE_PHRASES:
                review_body = review_body.replace(noise, "").strip()
        if review_body:
            reviews_list.append(review_body)

    # If extraction failed, expose the first block's structure for debugging.
    sample_structure = ""
    if not reviews_list and review_elements:
        first = review_elements[0]
        hooks = sorted({el.get("data-hook") for el in first.select("[data-hook]") if el.get("data-hook")})
        sample_structure = " data-hooks=" + ",".join(hooks)

    return reviews_list, len(review_elements), sample_structure
