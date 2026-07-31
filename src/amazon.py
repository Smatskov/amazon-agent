"""Read-only Amazon search tool isolated from agent orchestration."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, quote_plus, unquote_plus, urljoin, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


AMAZON_SEARCH_URL = "https://www.amazon.com/s"
AMAZON_HOME_URL = "https://www.amazon.com/"
MAX_SEARCH_RESULTS = 5
# Heavy result pages (a phone-case search is ~1.5 MB) need longer than a light one.
BROWSER_NAVIGATION_TIMEOUT_MS = 25_000
BROWSER_RESULTS_TIMEOUT_MS = 15_000
BROWSER_CLOSE_TIMEOUT_SECONDS = 5
DEFAULT_BROWSER_PROFILE_DIR = (
    Path.home() / "Library" / "Application Support" / "Amazon Agent" / "playwright-profile"
)
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
# Amazon serves different result layouts for different queries: some wrap the title in
# <h2><a>, others put the anchor around the <h2>, and the line-clamp class varies with
# title length. Keying off the ASIN card plus any product link covers every layout seen;
# the previous clamp-specific selector returned nothing at all for "iphone case".
PRODUCT_CARD_SELECTOR = "div[data-asin]:not([data-asin=''])"
PRODUCT_TITLE_SELECTOR = f"{PRODUCT_CARD_SELECTOR} a[href*='/dp/']"


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
    delivery: str | None = None


class AmazonSearchUnavailable(RuntimeError):
    """Amazon did not return a usable public search-results page."""


class AmazonProfileConfigurationError(RuntimeError):
    """The persistent browser profile is unsafe or unavailable."""


class AmazonCartUnavailable(RuntimeError):
    """The cart operation could not be completed safely."""


# Cart writes touch a real account, so the control that may be clicked is named
# exactly, never matched loosely. An id selector cannot resolve to "Buy Now".
ADD_TO_CART_BUTTON_ID = "add-to-cart-button"
CART_URL = "https://www.amazon.com/gp/cart/view.html"
CART_COUNT_SELECTOR = "#nav-cart-count"
# Any URL that could begin an order. Navigation to these is refused outright.
ORDERING_URL_FRAGMENTS = (
    "/gp/buy/",
    "/checkout",
    "buy-now",
    "go-to-checkout",
    "/gp/cart/desktop/go-to-checkout",
    "spc/handlers/display.html",
)


def cart_writes_enabled() -> bool:
    """Cart writes can be switched off without changing code."""
    return os.getenv("AMAZON_ENABLE_CART", "true").strip().casefold() != "false"


def _refuse_ordering_url(url: str) -> None:
    """Never navigate anywhere that could start or submit an order."""
    lowered = url.casefold()
    for fragment in ORDERING_URL_FRAGMENTS:
        if fragment in lowered:
            raise AmazonCartUnavailable(
                f"Refusing to navigate to an order URL: {fragment}"
            )


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

    def first_matching(self, field: str, pattern: re.Pattern[str]) -> str | None:
        """First value of a field that actually looks like the thing being read."""
        for value in self._values.get(field, []):
            cleaned = value.strip()
            if cleaned and pattern.search(cleaned):
                return cleaned
        return None


RATINGS_ARIA = re.compile(r'aria-label="([\d,]+)\s+ratings?"')
# Variation listings put a count such as "2 scents" in the first offscreen span, so a
# price must be recognised by shape rather than by being first.
PRICE_SHAPED = re.compile(r"[$£€]\s?\d")
DELIVERY_DATE = re.compile(
    r"(?:FREE delivery|delivery|Delivery|Get it|arrives)\D{0,20}"
    r"((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})"
)
# A "Join Prime" upsell means this account is not a Prime member, so a Prime badge
# would be misleading rather than helpful.
PRIME_UPSELL = re.compile(r"join prime", re.IGNORECASE)


def _result_metadata_from_html(html: str) -> tuple[str | None, float | None, int | None, bool | None]:
    """Read only visible pricing and rating metadata from one result-card fragment.

    Title and URL come from the Playwright locator rather than this parser; the
    parser still tracks the title anchor so its text is not mistaken for a price.
    """
    parser = _AmazonResultCardParser()
    parser.feed(html)
    # Amazon abbreviates the visible count ("(212.1K)") but keeps the exact number in
    # the accessibility label, so the label is the reliable source.
    ratings = RATINGS_ARIA.search(html)
    review_count = (
        _review_count_from_text(ratings.group(1))
        if ratings
        else _review_count_from_text(parser.first_value("review_count"))
    )
    prime = True if parser.prime_eligible and not PRIME_UPSELL.search(html) else None
    return (
        parser.first_matching("price", PRICE_SHAPED),
        _rating_from_text(parser.first_value("rating")),
        review_count,
        prime,
    )


HTML_TAG = re.compile(r"<[^>]+>")


def _delivery_from_html(html: str) -> str | None:
    """Return a delivery date only when Amazon states one on the card.

    Tags are stripped first: the date sits inside its own span, so matching against
    raw markup put 20+ characters of attributes between the label and the date.
    """
    text = unescape(HTML_TAG.sub(" ", html))
    match = DELIVERY_DATE.search(" ".join(text.split()))
    return match.group(1).strip() if match else None


async def _best_title(card, link) -> str | None:
    """Pick the fuller of the heading and the link text.

    Layouts disagree about which one holds the product name: for a phone-case search
    the heading has it and the anchor is empty, while for a shampoo search the heading
    is only the brand ("Head & Shoulders") and the anchor has the full title.
    """
    candidates = []
    heading = card.locator("h2").first
    if await heading.count():
        candidates.append(await _text_or_none(heading))
    candidates.append(await _text_or_none(link))
    usable = [value for value in candidates if value]
    return max(usable, key=len) if usable else None


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
    """Search runs in the background by default.

    A window flashing open on every Telegram message is poor UX. Headless is a
    display choice only: the same persistent profile and the same session are used,
    and no bot protection is bypassed. Set AMAZON_BROWSER_HEADLESS=false to watch.
    """
    return os.getenv("AMAZON_BROWSER_HEADLESS", "true").strip().casefold() != "false"


@asynccontextmanager
async def _persistent_browser_context(*, headless: bool | None = None):
    """Open the configured Chromium profile without altering its Amazon session."""
    profile = browser_profile_dir()
    profile.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile),
            headless=_browser_headless() if headless is None else headless,
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
    # Always visible regardless of configuration: this step exists to be used by a human.
    async with _persistent_browser_context(headless=False) as context:
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
                wait_until="domcontentloaded",
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
        cards = page.locator(PRODUCT_CARD_SELECTOR)
        for index in range(await cards.count()):
            if len(products) == MAX_SEARCH_RESULTS:
                break

            card = cards.nth(index)
            link = card.locator("a[href*='/dp/']").first
            if not await link.count():
                continue
            href = await link.get_attribute("href")
            if not href:
                continue
            url = urljoin(page.url, href)
            if not _is_amazon_product_url(url) or url in seen_urls:
                continue

            title = await _best_title(card, link)
            if not title:
                continue

            card_html = await card.inner_html()
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
                delivery=_delivery_from_html(card_html),
            )
            seen_urls.add(url)
            products.append(product)
        return products
    except PlaywrightTimeoutError as error:
        raise AmazonSearchUnavailable(
            "Amazon did not load a usable search page before the timeout."
        ) from error


async def _cart_count(page) -> int | None:
    """Read the header cart badge, which is how success is confirmed."""
    try:
        text = await page.locator(CART_COUNT_SELECTOR).first.get_attribute("aria-label")
        if text:
            digits = re.search(r"\d+", text)
            if digits:
                return int(digits.group())
        raw = await page.locator(CART_COUNT_SELECTOR).first.text_content()
        return int((raw or "").strip()) if (raw or "").strip().isdigit() else None
    except Exception:
        return None


async def add_to_cart(product_url: str, quantity: int = 1, *, visible: bool = False) -> int | None:
    """Add one product to the real Amazon cart. Never begins or submits an order.

    Returns the cart count after the click when Amazon reports one, so the caller can
    confirm the write really happened instead of assuming it did.
    """
    if not cart_writes_enabled():
        raise AmazonCartUnavailable("Cart writes are disabled (AMAZON_ENABLE_CART=false).")
    if not _is_amazon_product_url(product_url):
        raise AmazonCartUnavailable("Refusing a non-canonical Amazon product URL.")
    _refuse_ordering_url(product_url)

    async with _persistent_browser_context(headless=None if not visible else False) as context:
        page = await context.new_page()
        try:
            await page.goto(product_url, wait_until="domcontentloaded", timeout=25_000)
            before = await _cart_count(page)

            button = page.locator(f"#{ADD_TO_CART_BUTTON_ID}")
            if not await button.count():
                raise AmazonCartUnavailable(
                    "No Add to Cart control on this page; it may be unavailable or a variation picker."
                )
            # Defence in depth: confirm the resolved element is the intended control.
            resolved_id = await button.first.get_attribute("id")
            if resolved_id != ADD_TO_CART_BUTTON_ID:
                raise AmazonCartUnavailable(f"Unexpected control id {resolved_id!r}; refusing to click.")

            if quantity > 1:
                selector = page.locator("#quantity")
                if await selector.count():
                    try:
                        await selector.first.select_option(str(min(quantity, 30)))
                    except Exception:
                        print("[AMAZON] quantity selector rejected the value; adding one")

            await button.first.click()
            await page.wait_for_load_state("domcontentloaded", timeout=20_000)
            _refuse_ordering_url(page.url)
            after = await _cart_count(page)
            print(f"[AMAZON] add_to_cart cart_count before={before} after={after}")
            if after is None:
                raise AmazonCartUnavailable("Could not confirm the item reached the cart.")
            return after
        finally:
            await page.close()


CART_ROW_SELECTOR = "[data-asin][data-itemid], .sc-list-item[data-asin]"
CART_PRICE_SELECTOR = ".sc-item-price-block .a-price .a-offscreen"
CART_DELETE_SELECTOR = "input[value='Delete']"


@dataclass(frozen=True, slots=True)
class CartWriteResult:
    """What actually happened for one item, so nothing is reported as guessed."""

    url: str
    quantity: int
    added: bool
    detail: str | None = None


async def add_many_to_cart(items: list[tuple[str, int]]) -> list[CartWriteResult]:
    """Add several products in one browser session.

    One item failing must not abandon the rest, and every result reports what really
    happened rather than assuming the click worked.
    """
    if not cart_writes_enabled():
        raise AmazonCartUnavailable("Cart writes are disabled (AMAZON_ENABLE_CART=false).")

    results: list[CartWriteResult] = []
    async with _persistent_browser_context() as context:
        page = await context.new_page()
        try:
            for url, quantity in items:
                try:
                    if not _is_amazon_product_url(url):
                        raise AmazonCartUnavailable("Not a canonical Amazon product URL.")
                    _refuse_ordering_url(url)
                    await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                    button = page.locator(f"#{ADD_TO_CART_BUTTON_ID}")
                    if not await button.count():
                        raise AmazonCartUnavailable("No Add to Cart control on this page.")
                    if await button.first.get_attribute("id") != ADD_TO_CART_BUTTON_ID:
                        raise AmazonCartUnavailable("Unexpected control id; refusing to click.")
                    if quantity > 1:
                        selector = page.locator("#quantity")
                        if await selector.count():
                            try:
                                await selector.first.select_option(str(min(quantity, 30)))
                            except Exception:
                                pass
                    before = await _cart_count(page)
                    await button.first.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=20_000)
                    _refuse_ordering_url(page.url)
                    # A click that raises nothing is not proof. A variation page shows
                    # an Add to Cart button that does nothing until a size or scent is
                    # chosen, which previously reported success for an empty cart.
                    after = await _cart_count(page)
                    if after is None or (before is not None and after <= before):
                        raise AmazonCartUnavailable(
                            "Amazon did not confirm the item reached the cart; it may "
                            "need a size or colour chosen first."
                        )
                    results.append(CartWriteResult(url, quantity, True))
                except Exception as error:  # noqa: BLE001 - one failure must not stop the rest
                    results.append(CartWriteResult(url, quantity, False, str(error)[:120]))
            count = await _cart_count(page)
            print(f"[AMAZON] add_many_to_cart added={sum(r.added for r in results)} cart_count={count}")
        finally:
            await page.close()
    return results


async def read_cart() -> list[Product]:
    """Read the real Amazon cart. Read-only; never proceeds to checkout."""
    async with _persistent_browser_context() as context:
        page = await context.new_page()
        try:
            await page.goto(CART_URL, wait_until="domcontentloaded", timeout=25_000)
            _refuse_ordering_url(page.url)
            rows = page.locator(CART_ROW_SELECTOR)
            items: list[Product] = []
            for index in range(await rows.count()):
                row = rows.nth(index)
                title = await _text_or_none(
                    row.locator(".sc-product-title, .a-truncate-full").first
                )
                price = await _text_or_none(row.locator(CART_PRICE_SELECTOR).first)
                asin = await row.get_attribute("data-asin")
                if title:
                    items.append(
                        Product(
                            title=title,
                            price=price,
                            url=f"https://www.amazon.com/dp/{asin}" if asin else CART_URL,
                        )
                    )
            return items
        finally:
            await page.close()


async def remove_from_cart(asin: str, *, visible: bool = False) -> int | None:
    """Remove one item from the real Amazon cart by ASIN.

    Only the Delete control inside the matching cart row may be clicked, and the URL is
    re-checked afterwards so a mis-click cannot land on a checkout page unnoticed.
    """
    if not cart_writes_enabled():
        raise AmazonCartUnavailable("Cart writes are disabled (AMAZON_ENABLE_CART=false).")
    if not re.fullmatch(r"[A-Za-z0-9]{6,14}", asin or ""):
        raise AmazonCartUnavailable(f"Refusing an implausible ASIN: {asin!r}")

    async with _persistent_browser_context(headless=None if not visible else False) as context:
        page = await context.new_page()
        try:
            await page.goto(CART_URL, wait_until="domcontentloaded", timeout=25_000)
            _refuse_ordering_url(page.url)
            row = page.locator(f"{CART_ROW_SELECTOR}").filter(has=page.locator(f"[data-asin='{asin}']"))
            target = page.locator(f"[data-asin='{asin}']").first
            if not await target.count():
                raise AmazonCartUnavailable(f"{asin} is not in the cart.")
            delete = target.locator(CART_DELETE_SELECTOR).first
            if not await delete.count():
                raise AmazonCartUnavailable("No Delete control in that cart row.")
            await delete.click()
            await page.wait_for_load_state("domcontentloaded", timeout=20_000)
            _refuse_ordering_url(page.url)
            remaining = await _cart_count(page)
            print(f"[AMAZON] remove_from_cart asin={asin} cart_count_after={remaining}")
            return remaining
        finally:
            await page.close()


SEARCH_ATTEMPTS = 2


async def search_products(query: str) -> list[Product]:
    """Return up to five visible Amazon search results from the local profile.

    The first search after the browser starts pays a cold-start cost and was observed
    timing out where an immediate repeat succeeded, so one retry is made before
    reporting failure. A retry re-reads a public results page; it changes nothing.
    """
    last_error: Exception | None = None
    for attempt in range(1, SEARCH_ATTEMPTS + 1):
        try:
            async with _persistent_browser_context() as context:
                return await _search_in_context(context, query)
        except AmazonSearchUnavailable as error:
            last_error = error
            print(f"[AMAZON] search attempt {attempt} found no results: {error}")
        except PlaywrightError as error:
            last_error = AmazonSearchUnavailable(
                "The local Amazon browser profile is unavailable or already in use."
            )
            print(f"[AMAZON] search attempt {attempt} browser error: {error}")
    raise last_error
