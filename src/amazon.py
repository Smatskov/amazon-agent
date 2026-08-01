"""Read-only Amazon search tool isolated from agent orchestration."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import os
from datetime import datetime, timezone
from pathlib import Path
import re
from time import perf_counter
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
    unit_price: str | None = None
    image_url: str | None = None


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


# Ordering spends real money, so it is off unless deliberately switched on. A stale
# config, a forgotten test, or a copied .env cannot place an order by accident.
def ordering_enabled() -> bool:
    return os.getenv("AMAZON_ENABLE_ORDERING", "false").strip().casefold() == "true"


def max_order_total() -> float:
    """A hard ceiling on what one order may cost (AGENTS.md requires a price limit)."""
    try:
        return float(os.getenv("AMAZON_MAX_ORDER_TOTAL", "100"))
    except ValueError:
        return 100.0


ORDER_AUDIT_PATH = Path(
    os.getenv("AMAZON_ORDER_AUDIT_LOG", str(Path.home() / "Library" / "Application Support"
              / "Amazon Agent" / "orders.log"))
)


def _audit(event: str) -> None:
    """Append-only record of every order attempt, successful or not."""
    try:
        ORDER_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with ORDER_AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {event}\n")
    except Exception as error:  # noqa: BLE001 - logging must never break an order
        print(f"[AMAZON] could not write the order audit log: {error}")


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
# Amazon's own per-unit price, e.g. "($2.27/fluid ounce)" or "($0.83/count)". Copied
# verbatim rather than computed: dividing a price by a size read out of a title would
# invent a fact, and Amazon already states the one that is correct for the listing.
UNIT_PRICE_TEXT = re.compile(r"\(\s*(\$[\d,]+\.?\d*\s*/\s*[A-Za-z][A-Za-z ]{0,18}?)\s*\)")


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


def _unit_price_from_html(html: str) -> str | None:
    """Read the per-unit price Amazon printed on the card, or None when it printed none."""
    text = unescape(HTML_TAG.sub(" ", html))
    match = UNIT_PRICE_TEXT.search(" ".join(text.split()))
    if not match:
        return None
    return " ".join(match.group(1).split()).replace(" /", "/").replace("/ ", "/")


def _delivery_from_html(html: str) -> str | None:
    """Return a delivery date only when Amazon states one on the card.

    Tags are stripped first: the date sits inside its own span, so matching against
    raw markup put 20+ characters of attributes between the label and the date.
    """
    text = unescape(HTML_TAG.sub(" ", html))
    match = DELIVERY_DATE.search(" ".join(text.split()))
    return match.group(1).strip() if match else None


def _clean_title(value: str | None) -> str | None:
    """Collapse whitespace and drop undecodable characters Amazon sometimes serves.

    A raw U+FFFD in a title ("Oral-B Cavity Defense 123 Black Toothbrush <?> Medium")
    is not a product fact; it is a decoding artefact, so it is replaced with a dash.
    """
    if not value:
        return None
    cleaned = " ".join(value.replace("�", "-").split())
    return cleaned or None


async def _best_title(card, link) -> str | None:
    """Pick the fullest product name the card offers.

    Layouts disagree about which element holds it. Live probing of one results page
    found every Oral-B card carrying only the brand in both the heading and the anchor
    (`h2` = "Oral-B", anchor empty) while the result image's alt text held the complete
    name ("Oral-B Complete Deep Clean Soft Bristles Toothbrush 4 Count"). The image alt
    is therefore a first-class source, not a last resort: without it the user sees five
    identical "Oral-B" lines and cannot tell the options apart.
    """
    candidates = []
    heading = card.locator("h2").first
    if await heading.count():
        candidates.append(await _text_or_none(heading))
    candidates.append(await _text_or_none(link))
    image = card.locator("img.s-image").first
    if await image.count():
        try:
            candidates.append(await image.get_attribute("alt"))
        except Exception:
            pass
    usable = [cleaned for cleaned in (_clean_title(value) for value in candidates) if cleaned]
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


ASIN_IN_URL = re.compile(r"/dp/([A-Za-z0-9]{6,14})")


def asin_from_url(url: str) -> str | None:
    """Amazon's own identifier for the product, when the link carries one."""
    match = ASIN_IN_URL.search(url or "")
    return match.group(1) if match else None


# Internal alias kept so extraction code reads consistently with the rest of the module.
_asin_from_url = asin_from_url


def _usable_price(price: str | None) -> str | None:
    """Reject a price that cannot be a real offer.

    Live results include cards whose offscreen price span reads "$0.00" for listings
    with no purchasable offer. Sorting treats that as the cheapest option and would
    put an unbuyable item first, so it is recorded as no price rather than as free.
    """
    if not price:
        return None
    digits = re.search(r"\d[\d,]*(?:\.\d{1,2})?", price)
    if not digits:
        return None
    try:
        return None if float(digits.group().replace(",", "")) <= 0 else price
    except ValueError:
        return None


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


def _search_url(query: str, max_price: float | None, min_price: float | None = None) -> str:
    """Build the results URL, asking Amazon itself to apply the price bounds.

    Verified live: `low-price`/`high-price` are honoured and return a genuinely
    different result set. Two other approaches were tried and rejected — `rh=p_36:...`
    and `s=price-asc-rank` both returned "Sorry! Something went wrong!", so neither is
    used.
    """
    url = f"{AMAZON_SEARCH_URL}?k={quote_plus(query)}"
    if max_price is not None or min_price is not None:
        low = f"{min_price:g}" if min_price is not None and min_price > 0 else ""
        high = f"{max_price:g}" if max_price is not None and max_price > 0 else ""
        url += f"&low-price={low}&high-price={high}"
    return url


async def _search_in_context(
    context, query: str, *, max_price: float | None = None, min_price: float | None = None
) -> list[Product]:
    """Extract public product records using an already-open persistent context."""
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Amazon search requires a non-empty query.")

    page, already_matching_query = await _page_for_query(context, normalized_query)
    # A page already showing this query is showing it *unfiltered*, so it must not be
    # reused when a price ceiling is being applied.
    if max_price is not None or min_price is not None:
        already_matching_query = False
    search_url = _search_url(normalized_query, max_price, min_price)
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
        seen_asins: set[str] = set()
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
            # Amazon nests result cards, so the same product is reachable more than once
            # under URLs that differ only by tracking path. Identity is the ASIN.
            asin = _asin_from_url(url)
            if asin and asin in seen_asins:
                continue

            title = await _best_title(card, link)
            if not title:
                continue

            card_html = await card.inner_html()
            price, rating, review_count, prime_eligible = _result_metadata_from_html(
                card_html
            )
            price = _usable_price(price)
            image = card.locator("img.s-image").first
            image_url = await image.get_attribute("src") if await image.count() else None
            product = Product(
                title=title,
                price=price,
                url=url,
                rating=rating,
                review_count=review_count,
                prime_eligible=prime_eligible,
                delivery=_delivery_from_html(card_html),
                unit_price=_unit_price_from_html(card_html),
                image_url=image_url,
            )
            seen_urls.add(url)
            if asin:
                seen_asins.add(asin)
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


CART_ROW_SELECTOR = "[data-asin][data-itemid], .sc-list-item[data-asin]"
CART_PRICE_SELECTOR = ".sc-item-price-block .a-price .a-offscreen"
CART_DELETE_SELECTOR = "input[value='Delete']"


ADD_TO_CART_WAIT_MS = 10_000


async def _add_to_cart_button(page):
    """Return the Add to Cart control, waiting for the product page to settle first.

    Two things made this report "No Add to Cart control on this page" for items that
    were in stock and perfectly addable:

    - The buybox is attached after DOMContentLoaded, so counting straight after
      navigation sees nothing.
    - Product pages redirect to a variation URL (`/dp/B00CC6XSSQ` becomes
      `/dp/B00CC6XSSQ?th=1`) *after* load. A locator wait started before that
      navigation times out even though the button appears moments later.

    So the page is settled first and the button is polled, rather than waiting on a
    single locator across a navigation that may replace the document underneath it.
    """
    deadline = perf_counter() + ADD_TO_CART_WAIT_MS / 1000
    button = page.locator(f"#{ADD_TO_CART_BUTTON_ID}")
    try:
        await page.wait_for_load_state("load", timeout=ADD_TO_CART_WAIT_MS)
    except PlaywrightTimeoutError:
        pass
    while perf_counter() < deadline:
        _refuse_ordering_url(page.url)
        try:
            if await button.count():
                break
        except PlaywrightError:
            pass  # a navigation swapped the document; look again
        await page.wait_for_timeout(250)

    if not await button.count():
        raise AmazonCartUnavailable(
            "No Add to Cart control on this page; it may be unavailable, a variation "
            "picker, or sold only by third-party sellers."
        )
    # Defence in depth: confirm the resolved element is the intended control.
    resolved_id = await button.first.get_attribute("id")
    if resolved_id != ADD_TO_CART_BUTTON_ID:
        raise AmazonCartUnavailable(f"Unexpected control id {resolved_id!r}; refusing to click.")
    return button


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

    # Validate before opening anything. A URL that can never be written to a cart must
    # not cost a browser launch, and a list made entirely of bad URLs must not open one
    # at all.
    results: list[CartWriteResult] = []
    writable: list[tuple[str, int]] = []
    for url, quantity in items:
        try:
            if not _is_amazon_product_url(url):
                raise AmazonCartUnavailable("Not a canonical Amazon product URL.")
            _refuse_ordering_url(url)
            writable.append((url, quantity))
        except Exception as error:  # noqa: BLE001 - reported per item, never raised
            results.append(CartWriteResult(url, quantity, False, str(error)[:120]))
    if not writable:
        return results

    async with _persistent_browser_context() as context:
        page = await context.new_page()
        try:
            for url, quantity in writable:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                    button = await _add_to_cart_button(page)
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


@dataclass(frozen=True, slots=True)
class Variant:
    """One buyable child of a variation listing, identified by its own ASIN."""

    asin: str
    label: str
    url: str


# Amazon ships the whole variation map inline as
# "dimensionValuesDisplayData":{"B0FTHJCPFQ":["Swagger","3.8 Ounce (Pack of 1)"],...}.
# Reading that is exact: every entry is a real child ASIN with the values that
# identify it. Clicking swatches to discover them would be guesswork by comparison.
DIMENSION_VALUES = re.compile(
    r'"dimensionValuesDisplayData"\s*:\s*(\{.*?\})\s*,\s*"', re.DOTALL
)
MAX_VARIANTS = 12


def _variants_from_html(html: str) -> list[Variant]:
    """Parse the inline variation map, ignoring anything that is not a child ASIN."""
    import json

    match = DIMENSION_VALUES.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return []
    variants = []
    for asin, values in data.items():
        if not re.fullmatch(r"[A-Za-z0-9]{6,14}", str(asin)):
            continue
        label = " · ".join(
            " ".join(str(value).split()) for value in (values or []) if str(value).strip()
        )
        if label:
            variants.append(Variant(asin, label, f"https://www.amazon.com/dp/{asin}"))
    return variants[:MAX_VARIANTS]


async def read_variants(product_url: str) -> list[Variant]:
    """List the buyable children of a variation listing, or [] when there are none.

    A variation parent has no fixed identity — scent, size, and pack are chosen on the
    product page — so adding one to a cart either fails or is ambiguous. Resolving to
    a child ASIN first makes the thing that gets added exactly the thing that was
    chosen. Read-only; navigates to a product page and nothing else.
    """
    if not _is_amazon_product_url(product_url):
        return []
    _refuse_ordering_url(product_url)
    try:
        async with _persistent_browser_context() as context:
            page = await context.new_page()
            try:
                await page.goto(product_url, wait_until="domcontentloaded", timeout=25_000)
                await page.wait_for_timeout(1_500)
                return _variants_from_html(await page.content())
            finally:
                await page.close()
    except Exception as error:  # noqa: BLE001 - a failed read must not block the add
        print(f"[AMAZON] could not read variants: {error}")
        return []


PRODUCT_TITLE_ID = "#productTitle"
PRODUCT_PRICE_SELECTOR = "#corePrice_feature_div .a-offscreen, .priceToPay .a-offscreen, #price_inside_buybox"


async def read_product(product_url: str) -> Product | None:
    """Read title and price from one product page. Read-only, never orders."""
    if not _is_amazon_product_url(product_url):
        return None
    _refuse_ordering_url(product_url)
    try:
        async with _persistent_browser_context() as context:
            page = await context.new_page()
            try:
                await page.goto(product_url, wait_until="domcontentloaded", timeout=25_000)
                await page.wait_for_timeout(1_200)
                title = _clean_title(await _text_or_none(page.locator(PRODUCT_TITLE_ID).first))
                price = _usable_price(
                    await _text_or_none(page.locator(PRODUCT_PRICE_SELECTOR).first)
                )
                return Product(title=title, price=price, url=product_url) if title else None
            finally:
                await page.close()
    except Exception as error:  # noqa: BLE001
        print(f"[AMAZON] could not read product: {error}")
        return None


PAYMENTS_URL = "https://www.amazon.com/cpe/managepaymentmethods"
GLOW_SELECTOR = "#glow-ingress-block, #nav-global-location-slot"
MASKED_CARD = re.compile(r"[\u2022*x]{2,}\s*(\d{4})", re.IGNORECASE)
CARD_BRAND = re.compile(r"\b(Visa|Mastercard|American Express|Amex|Discover)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Destination:
    """Where an order would ship and what would pay for it, as Amazon reports it.

    Read-only and best-effort. Neither value is entered, changed, or stored anywhere;
    they exist so the user can see what an order would do before it does it.
    """

    address_label: str | None = None
    card_label: str | None = None


async def read_destination() -> Destination:
    """Read the default delivery location and card, without touching checkout.

    The location comes from the cart page's own header rather than the address book:
    Amazon puts `/a/addresses` behind a fresh sign-in even for a valid session, and
    this application never authenticates. The card is read from the payments page,
    masked exactly as Amazon prints it.
    """
    address = card = None
    try:
        return await _read_destination_in_browser()
    except Exception as error:  # noqa: BLE001 - display only; never break a reply
        print(f"[AMAZON] could not read destination: {error}")
        return Destination(None, None)


async def _read_destination_in_browser() -> Destination:
    address = card = None
    async with _persistent_browser_context() as context:
        page = await context.new_page()
        try:
            await page.goto(CART_URL, wait_until="domcontentloaded", timeout=25_000)
            _refuse_ordering_url(page.url)
            await page.wait_for_timeout(1_500)
            raw = await _text_or_none(page.locator(GLOW_SELECTOR).first)
            if raw:
                address = " ".join(raw.replace("\u200c", "").split())
                address = re.sub(r"^Deliver to\s*", "", address, flags=re.IGNORECASE) or None

            await page.goto(PAYMENTS_URL, wait_until="domcontentloaded", timeout=25_000)
            _refuse_ordering_url(page.url)
            await page.wait_for_timeout(2_500)
            body = " ".join((await page.locator("body").inner_text()).split())
            masked = MASKED_CARD.search(body)
            if masked:
                brand = CARD_BRAND.search(body)
                card = f"{brand.group(1) if brand else 'Card'} ending {masked.group(1)}"
        except Exception as error:  # noqa: BLE001 - display only; never break a reply
            print(f"[AMAZON] could not read destination: {error}")
        finally:
            await page.close()
    return Destination(address, card)


PROCEED_TO_CHECKOUT = 'input[name="proceedToRetailCheckout"], #sc-buy-box-ptc-button input'
PLACE_ORDER_SELECTOR = (
    '#placeYourOrder input, input[name="placeYourOrder1"], #submitOrderButtonId input, '
    '#bottomSubmitOrderButtonId input, #placeYourOrder, #submitOrderButtonId'
)
CART_SUBTOTAL_SELECTOR = "#sc-subtotal-amount-activecart, #sc-subtotal-amount-buybox"
ORDER_CONFIRMATION_URL = re.compile(r"/gp/buy/thankyou|order-?confirm|thankyou", re.IGNORECASE)
ORDER_ID = re.compile(r"\b(\d{3}-\d{7}-\d{7})\b")
# Amazon states a payment problem in prose rather than a status code.
DECLINE_TEXT = re.compile(
    r"payment method was declined|card was declined|declined by (?:your|the) bank|"
    r"there(?:'s| is) a problem with your payment|revise payment|payment revision needed|"
    r"we cannot process your payment|unable to process your payment",
    re.IGNORECASE,
)
SIGN_IN_URL = re.compile(r"/ap/signin|/ap/cvf|forgotpassword", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class OrderResult:
    """What actually happened, never what was assumed to happen."""

    placed: bool
    order_id: str | None = None
    order_url: str | None = None
    detail: str | None = None
    needs_sign_in: bool = False
    declined: bool = False


def _amount(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"\d[\d,]*(?:\.\d{2})?", text.replace("\xa0", " "))
    try:
        return float(match.group().replace(",", "")) if match else None
    except ValueError:
        return None


async def place_order(*, max_total: float | None = None) -> OrderResult:
    """Place the real Amazon order for whatever is in the cart.

    Every refusal below is a deliberate control, not an accident of implementation:

    - Ordering is off unless `AMAZON_ENABLE_ORDERING=true`, so no stale configuration
      can spend money.
    - The cart total is checked against a ceiling before checkout is even opened, and
      again against the order total Amazon states on the review page. A total that
      grew between those two reads is refused rather than paid.
    - Amazon requires a fresh sign-in before checkout (`max_auth_age=900`). This
      application never authenticates, so that redirect is reported as a failure for
      the user to resolve, never worked around.
    - Every attempt is written to an append-only audit log.
    """
    ceiling = max_order_total() if max_total is None else max_total
    if not ordering_enabled():
        return OrderResult(False, detail="Ordering is switched off (AMAZON_ENABLE_ORDERING is not true).")

    async with _persistent_browser_context() as context:
        page = await context.new_page()
        try:
            await page.goto(CART_URL, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2_000)

            subtotal = _amount(await _text_or_none(page.locator(CART_SUBTOTAL_SELECTOR).first))
            if subtotal is None:
                _audit("REFUSED no-readable-subtotal")
                return OrderResult(False, detail="I could not read the cart total, so I did not order.")
            if subtotal > ceiling:
                _audit(f"REFUSED over-ceiling subtotal={subtotal} ceiling={ceiling}")
                return OrderResult(
                    False,
                    detail=f"The cart comes to ${subtotal:.2f}, above the ${ceiling:.2f} limit. "
                           "Raise AMAZON_MAX_ORDER_TOTAL if that is intended.",
                )

            proceed = page.locator(PROCEED_TO_CHECKOUT).first
            if not await proceed.count():
                _audit("REFUSED no-proceed-control")
                return OrderResult(False, detail="Amazon showed no checkout button on the cart.")
            await proceed.click()
            await page.wait_for_load_state("domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(4_000)

            if SIGN_IN_URL.search(page.url):
                _audit("BLOCKED sign-in-required")
                return OrderResult(
                    False,
                    needs_sign_in=True,
                    detail="Amazon wants you to sign in again before it will accept an order.",
                )

            button = page.locator(PLACE_ORDER_SELECTOR).first
            try:
                await button.wait_for(state="attached", timeout=15_000)
            except PlaywrightTimeoutError:
                pass
            if not await button.count():
                _audit("REFUSED no-place-order-control")
                return OrderResult(
                    False,
                    detail="I could not find Amazon's Place Order button, so nothing was submitted.",
                )

            body = " ".join((await page.locator("body").inner_text()).split())
            stated = _amount((re.search(r"Order total:?\s*(\$[\d,]+\.\d{2})", body, re.I) or [None, None])[1])
            if stated is not None and stated > ceiling:
                _audit(f"REFUSED order-total-over-ceiling stated={stated} ceiling={ceiling}")
                return OrderResult(
                    False,
                    detail=f"Amazon's order total is ${stated:.2f}, above the ${ceiling:.2f} limit. "
                           "Nothing was submitted.",
                )

            _audit(f"PLACING subtotal={subtotal} stated_total={stated} ceiling={ceiling}")
            await button.click()
            await page.wait_for_load_state("domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)

            after = " ".join((await page.locator("body").inner_text()).split())
            if DECLINE_TEXT.search(after):
                _audit("FAILED payment-declined")
                return OrderResult(
                    False,
                    declined=True,
                    detail="Amazon rejected the payment method. Nothing was ordered.",
                )

            order_id = (ORDER_ID.search(after) or [None, None])[1]
            if ORDER_CONFIRMATION_URL.search(page.url) or order_id:
                _audit(f"PLACED order_id={order_id}")
                return OrderResult(
                    True,
                    order_id=order_id,
                    order_url="https://www.amazon.com/gp/css/order-history",
                    detail=None,
                )
            _audit("UNCONFIRMED no-confirmation-page")
            return OrderResult(
                False,
                detail="Amazon did not show a confirmation, so I cannot tell you the order "
                       "went through. Check your Amazon orders before trying again.",
            )
        except Exception as error:  # noqa: BLE001 - the reply must never be an exception
            _audit(f"ERROR {str(error)[:120]}")
            return OrderResult(False, detail=f"The order could not be completed ({str(error)[:100]}).")
        finally:
            await page.close()


SEARCH_ATTEMPTS = 2


async def search_products(
    query: str, *, max_price: float | None = None, min_price: float | None = None
) -> list[Product]:
    """Return up to five visible Amazon search results from the local profile.

    `max_price` is applied by Amazon, not by us. Filtering a page of results we already
    hold can only remove; asking Amazon for the cheaper page actually finds things. For
    "dove body wash" the unfiltered page starts at $10.97, while the same query under a
    $10 ceiling returns six listings from $5.47.

    The first search after the browser starts pays a cold-start cost and was observed
    timing out where an immediate repeat succeeded, so one retry is made before
    reporting failure. A retry re-reads a public results page; it changes nothing.
    """
    last_error: Exception | None = None
    for attempt in range(1, SEARCH_ATTEMPTS + 1):
        try:
            async with _persistent_browser_context() as context:
                return await _search_in_context(
                    context, query, max_price=max_price, min_price=min_price
                )
        except AmazonSearchUnavailable as error:
            last_error = error
            print(f"[AMAZON] search attempt {attempt} found no results: {error}")
        except PlaywrightError as error:
            last_error = AmazonSearchUnavailable(
                "The local Amazon browser profile is unavailable or already in use."
            )
            print(f"[AMAZON] search attempt {attempt} browser error: {error}")
    raise last_error
