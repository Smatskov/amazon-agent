"""Read-only Amazon search tool isolated from agent orchestration."""

from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin

from playwright.async_api import async_playwright


AMAZON_SEARCH_URL = "https://www.amazon.com/s"
MAX_SEARCH_RESULTS = 5

# TODO: Add a separately authorized, read-only Amazon order-history lookup interface.


@dataclass(frozen=True, slots=True)
class Product:
    """The small, stable product shape returned by the initial Amazon tool."""

    title: str
    price: str | None
    url: str
    rating: float | None = None
    review_count: int | None = None
    availability: str | None = None
    prime_eligible: bool | None = None


async def _text_or_none(locator) -> str | None:
    """Return stripped locator text when the optional element is present."""
    try:
        text = await locator.text_content()
    except Exception:
        return None
    return text.strip() if text and text.strip() else None


def _rating_from_text(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(text.split()[0])
    except (IndexError, ValueError):
        return None


def _review_count_from_text(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


async def search_products(query: str) -> list[Product]:
    """Return up to five visible Amazon search results without signing in."""
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Amazon search requires a non-empty query.")

    search_url = f"{AMAZON_SEARCH_URL}?k={quote_plus(normalized_query)}"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(search_url, wait_until="domcontentloaded")
            await page.wait_for_selector(
                'div[data-component-type="s-search-result"]', timeout=15_000
            )

            products: list[Product] = []
            results = page.locator('div[data-component-type="s-search-result"]')
            for index in range(await results.count()):
                if len(products) == MAX_SEARCH_RESULTS:
                    break

                result = results.nth(index)
                if not await result.is_visible():
                    continue

                link = result.locator("h2 a").first
                title = await _text_or_none(link)
                href = await link.get_attribute("href")
                if not title or not href:
                    continue

                rating_text = await _text_or_none(result.locator(".a-icon-alt").first)
                review_count_text = await _text_or_none(
                    result.locator(".a-size-base.s-underline-text").first
                )
                availability = await _text_or_none(
                    result.locator(".a-color-price").first
                )
                prime_eligible = (
                    True
                    if await result.locator(".a-icon-prime").count()
                    else None
                )
                products.append(
                    Product(
                        title=title,
                        price=await _text_or_none(
                            result.locator(".a-price .a-offscreen").first
                        ),
                        url=urljoin(page.url, href),
                        rating=_rating_from_text(rating_text),
                        review_count=_review_count_from_text(review_count_text),
                        availability=availability,
                        prime_eligible=prime_eligible,
                    )
                )

            return products
        finally:
            await browser.close()
