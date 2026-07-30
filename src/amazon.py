"""Read-only Amazon search tool isolated from agent orchestration."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from html.parser import HTMLParser
import os
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, quote_plus, unquote_plus, urljoin, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


AMAZON_SEARCH_URL = "https://www.amazon.com/s"
AMAZON_HOME_URL = "https://www.amazon.com/"
MAX_SEARCH_RESULTS = 5
BROWSER_NAVIGATION_TIMEOUT_MS = 8_000
BROWSER_RESULTS_TIMEOUT_MS = 6_000
BROWSER_CLOSE_TIMEOUT_SECONDS = 5
DEFAULT_BROWSER_PROFILE_DIR = (
    Path.home() / "Library" / "Application Support" / "Amazon Agent" / "playwright-profile"
)
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
PRODUCT_TITLE_SELECTOR = (
    "a.a-link-normal.s-line-clamp-3[href*='/dp/'], "
    "h2 a.a-link-normal[href*='/dp/'], h2 a[href*='/dp/']"
)

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


class AmazonSearchUnavailable(RuntimeError):
    """Amazon did not return a usable public search-results page."""


class AmazonProfileConfigurationError(RuntimeError):
    """The persistent browser profile is unsafe or unavailable."""


class AmazonWorkflowGateway(Protocol):
    """Future typed boundary; Milestone 1 does not invoke these consequential methods."""

    async def lookup_recent_orders(self, query: str) -> list[Product]: ...

    async def search_products(self, query: str) -> list[Product]: ...

    async def fetch_product_details(self, product_url: str) -> Product: ...

    async def add_to_cart(self, product_url: str, quantity: int) -> None: ...

    async def inspect_checkout(self) -> dict[str, str]: ...

    async def place_confirmed_order(self, confirmation_version: int) -> str: ...


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


class _AmazonResultCardParser(HTMLParser):
    """Parse one already-located Amazon result card without inferring absent facts."""

    def __init__(self) -> None:
        super().__init__()
        self._h2_depth = 0
        self._active_field: str | None = None
        self._field_depth = 0
        self._values: dict[str, list[str]] = {}
        self.href: str | None = None
        self.prime_eligible = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "h2":
            self._h2_depth += 1
        if tag == "a" and (
            self._h2_depth or "s-line-clamp-3" in classes
        ) and not self.href:
            self.href = attributes.get("href")
            self._start_field("title")
        elif "a-offscreen" in classes:
            self._start_field("price")
        elif "a-icon-alt" in classes:
            self._start_field("rating")
        elif {"a-size-base", "s-underline-text"}.issubset(classes):
            self._start_field("review_count")
        if "a-icon-prime" in classes:
            self.prime_eligible = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2" and self._h2_depth:
            self._h2_depth -= 1
        if self._active_field:
            self._field_depth -= 1
            if self._field_depth == 0:
                self._active_field = None

    def handle_data(self, data: str) -> None:
        if self._active_field:
            self._values.setdefault(self._active_field, []).append(data)

    def _start_field(self, field: str) -> None:
        if self._active_field is None:
            self._active_field = field
            self._field_depth = 1

    def first_value(self, field: str) -> str | None:
        for value in self._values.get(field, []):
            cleaned = value.strip()
            if cleaned:
                return cleaned
        return None


def _result_metadata_from_html(html: str) -> tuple[str | None, float | None, int | None, bool | None]:
    """Read only visible pricing and rating metadata from one result-card fragment.

    Title and URL come from the Playwright locator rather than this parser; the
    parser still tracks the title anchor so its text is not mistaken for a price.
    """
    parser = _AmazonResultCardParser()
    parser.feed(html)
    return (
        parser.first_value("price"),
        _rating_from_text(parser.first_value("rating")),
        _review_count_from_text(parser.first_value("review_count")),
        True if parser.prime_eligible else None,
    )


def browser_profile_dir() -> Path:
    """Return a local persistent profile directory that is outside the repository."""
    configured = os.getenv("AMAZON_BROWSER_PROFILE_DIR")
    profile = Path(configured).expanduser() if configured else DEFAULT_BROWSER_PROFILE_DIR
    profile = profile.resolve()
    if profile == REPOSITORY_ROOT or REPOSITORY_ROOT in profile.parents:
        raise AmazonProfileConfigurationError(
            "AMAZON_BROWSER_PROFILE_DIR must be outside the repository."
        )
    return profile


def _browser_headless() -> bool:
    """Visible browsing is the safe default; headless mode is explicit only."""
    return os.getenv("AMAZON_BROWSER_HEADLESS", "false").strip().casefold() == "true"


@asynccontextmanager
async def _persistent_browser_context():
    """Open the configured Chromium profile without altering its Amazon session."""
    profile = browser_profile_dir()
    profile.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile),
            headless=_browser_headless(),
            viewport={"width": 1440, "height": 1000},
        )
        try:
            yield context
        finally:
            try:
                await asyncio.wait_for(
                    context.close(), timeout=BROWSER_CLOSE_TIMEOUT_SECONDS
                )
            except TimeoutError:
                print("[AMAZON] browser context close timed out")


async def open_profile_for_manual_sign_in() -> None:
    """Open Amazon visibly and wait for the user to complete any manual sign-in."""
    async with _persistent_browser_context() as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(AMAZON_HOME_URL, wait_until="domcontentloaded", timeout=30_000)
        print(
            "[AMAZON] visible persistent profile is open. "
            "Complete sign-in or any challenge manually, then press Return here."
        )
        await asyncio.to_thread(input)


def _query_matches_page(page_url: str, query: str) -> bool:
    """Reuse only a currently open Amazon search page for the exact requested query."""
    parsed = urlparse(page_url)
    if "amazon." not in parsed.netloc.casefold() or parsed.path != "/s":
        return False
    page_query = parse_qs(parsed.query).get("k", [""])[0]
    return unquote_plus(page_query).strip().casefold() == query.strip().casefold()


def _is_amazon_product_url(url: str) -> bool:
    """Allow only canonical Amazon product links, never advertising redirects."""
    return urlparse(url).hostname in {"amazon.com", "www.amazon.com"}


async def _page_for_query(context, query: str):
    """Prefer the matching visible Amazon tab; never reuse a different search result page."""
    pages = list(context.pages)
    matching_pages = [page for page in pages if _query_matches_page(page.url, query)]
    if matching_pages:
        return matching_pages[-1], True

    amazon_pages = [page for page in pages if "amazon." in urlparse(page.url).netloc.casefold()]
    if amazon_pages:
        return amazon_pages[-1], False
    return (pages[-1] if pages else await context.new_page()), False


async def _search_in_context(context, query: str) -> list[Product]:
    """Extract public product records using an already-open persistent context."""
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Amazon search requires a non-empty query.")

    page, already_matching_query = await _page_for_query(context, normalized_query)
    search_url = f"{AMAZON_SEARCH_URL}?k={quote_plus(normalized_query)}"
    try:
        if not already_matching_query:
            await page.goto(
                search_url,
                wait_until="commit",
                timeout=BROWSER_NAVIGATION_TIMEOUT_MS,
            )
        try:
            product_links = page.locator(PRODUCT_TITLE_SELECTOR)
            await product_links.first.wait_for(
                state="attached",
                timeout=BROWSER_RESULTS_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError as error:
            title = (await page.title()).strip()
            if "sorry" in title.casefold():
                raise AmazonSearchUnavailable(
                    "Amazon returned an interstitial instead of search results."
                ) from error
            raise AmazonSearchUnavailable(
                "Amazon did not return visible search results before the timeout."
            ) from error

        products: list[Product] = []
        seen_urls: set[str] = set()
        for index in range(await product_links.count()):
            if len(products) == MAX_SEARCH_RESULTS:
                break

            link = product_links.nth(index)
            if not await link.is_visible():
                continue
            title = await _text_or_none(link)
            href = await link.get_attribute("href")
            if not title or not href:
                continue
            url = urljoin(page.url, href)
            if not _is_amazon_product_url(url) or url in seen_urls:
                continue

            card = link.locator(
                "xpath=ancestor::div[@data-asin and string-length(@data-asin) > 0][1]"
            )
            card_html = await card.inner_html() if await card.count() else ""
            price, rating, review_count, prime_eligible = _result_metadata_from_html(
                card_html
            )
            product = Product(
                title=title,
                price=price,
                url=url,
                rating=rating,
                review_count=review_count,
                prime_eligible=prime_eligible,
            )
            seen_urls.add(url)
            products.append(product)
        return products
    except PlaywrightTimeoutError as error:
        raise AmazonSearchUnavailable(
            "Amazon did not load a usable search page before the timeout."
        ) from error


async def search_products(query: str) -> list[Product]:
    """Return up to five visible Amazon search results from the local profile."""
    try:
        async with _persistent_browser_context() as context:
            return await _search_in_context(context, query)
    except PlaywrightError as error:
        raise AmazonSearchUnavailable(
            "The local Amazon browser profile is unavailable or already in use."
        ) from error
