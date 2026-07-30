"""Read-only DOM structure probe for repairing selectors.

Prints structural facts (class names, element counts, button ids) so selectors can be
written against what Amazon actually serves. It never clicks, never submits, and never
prints order numbers, addresses, payment details, or account information.
"""

import argparse
import asyncio
import json
import re

import amazon


async def probe_search(context, query: str) -> dict:
    page, _ = await amazon._page_for_query(context, query)
    await page.goto(
        f"{amazon.AMAZON_SEARCH_URL}?k={query.replace(' ', '+')}",
        wait_until="commit",
        timeout=15_000,
    )
    links = page.locator(amazon.PRODUCT_TITLE_SELECTOR)
    await links.first.wait_for(state="attached", timeout=10_000)
    card = links.first.locator("xpath=ancestor::div[@data-asin and string-length(@data-asin) > 0][1]")
    html = await card.inner_html()
    classes = sorted({c for c in re.findall(r'class="([^"]+)"', html) for c in c.split()})
    return {
        "probe": "search_card",
        "card_html_length": len(html),
        "classes_present": classes,
        "has_a_icon_prime": "a-icon-prime" in html,
        "review_link_snippets": re.findall(r'<a[^>]*s-underline-text[^>]*>.{0,120}', html)[:2],
        "aria_labels": re.findall(r'aria-label="([^"]{0,80})"', html)[:12],
        "offscreen_values": re.findall(r'a-offscreen[^>]*>([^<]{0,40})', html)[:6],
    }


async def probe_product(context, url: str) -> dict:
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
    result = {"probe": "product_page", "url_is_amazon": url.startswith("https://www.amazon.com/")}
    for name, selector in (
        ("add_to_cart_button", "#add-to-cart-button"),
        ("buy_now_button", "#buy-now-button"),
        ("quantity_select", "#quantity"),
        ("availability", "#availability"),
        ("delivery_block", "#mir-layout-DELIVERY_BLOCK"),
        ("delivery_promise", "[data-csa-c-delivery-time]"),
        ("primary_delivery", "#deliveryBlockMessage"),
    ):
        locator = page.locator(selector)
        count = await locator.count()
        text = None
        if count and name in {"availability", "delivery_block", "primary_delivery"}:
            raw = await locator.first.text_content()
            text = " ".join((raw or "").split())[:160]
        result[name] = {"count": count, "text": text}
    await page.close()
    return result


async def probe_orders(context) -> dict:
    page = await context.new_page()
    await page.goto("https://www.amazon.com/gp/css/order-history", wait_until="domcontentloaded", timeout=25_000)
    title = (await page.title()).strip()
    signed_in = "sign in" not in title.casefold() and "amazon sign" not in title.casefold()
    result = {"probe": "order_history", "page_title_kind": "orders" if signed_in else "sign-in-required"}
    for name, selector in (
        ("order_cards_js", ".order-card"),
        ("order_cards_legacy", ".a-box-group.a-spacing-base.order"),
        ("product_links", "a[href*='/product-reviews/'], a.a-link-normal[href*='/dp/']"),
        ("item_titles", ".yohtmlc-product-title, .a-col-right .a-link-normal"),
        ("delivery_status", ".yohtmlc-shipment-status-primaryText, .js-shipment-info-container"),
    ):
        result[name] = await page.locator(selector).count()
    # Sample only enough title text to confirm extraction works.
    titles = page.locator(".yohtmlc-product-title, .a-col-right .a-link-normal")
    samples = []
    for index in range(min(2, await titles.count())):
        raw = await titles.nth(index).text_content()
        samples.append(" ".join((raw or "").split())[:60])
    result["title_samples"] = samples
    await page.close()
    return result


async def main(args) -> None:
    async with amazon._persistent_browser_context() as context:
        if args.search:
            print(json.dumps(await probe_search(context, args.search), sort_keys=True), flush=True)
        if args.product:
            print(json.dumps(await probe_product(context, args.product), sort_keys=True), flush=True)
        if args.orders:
            print(json.dumps(await probe_orders(context), sort_keys=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--search")
    parser.add_argument("--product")
    parser.add_argument("--orders", action="store_true")
    asyncio.run(main(parser.parse_args()))
