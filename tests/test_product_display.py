"""Presentation shortens and arranges stored facts; it must never add one.

Output is Telegram HTML. The previous version emitted markdown, which Telegram sent
verbatim so the user saw literal `**Pick one:**`.
"""

import pytest

import menu
import product_display
import ranking
from menu import MenuAction, MenuOption
from ranking import RankedCandidates, SortPreference
from workflow_models import Candidate, CartLine


def _candidate(title, price=None, price_text=None, rating=None, reviews=None, delivery=None):
    return Candidate(
        candidate_id=title[:12],
        title=title,
        brand=None,
        price=price,
        delivery_label=delivery,
        rating=rating,
        price_text=price_text,
        review_count=reviews,
        source_url="https://www.amazon.com/dp/example",
    )


def _actions():
    return [
        MenuOption(MenuAction.NARROW, "Narrow these results"),
        MenuOption(MenuAction.CANCEL, "Start over"),
    ]


# --- titles -------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "Amazon Basics 48 Pack AA Alkaline High-Performance Batteries, 1.5 Volt",
            "Amazon Basics 48 Pack AA Alkaline High-Performance Batteries",
        ),
        ("Energizer MAX AA Batteries, 24 Count", "Energizer MAX AA Batteries, 24 Count"),
        (
            "Jockey Men's Classic Crew Neck T-Shirt, White, Medium, 3 Pack",
            "Jockey Men's Classic Crew Neck T-Shirt, White, Medium, 3 Pack",
        ),
        ("Short Title", "Short Title"),
    ],
)
def test_display_title_keeps_brand_product_and_variant_facts(raw, expected):
    assert product_display.display_title(raw) == expected


def test_marketing_filler_is_dropped():
    """"Packaging May Vary" tells a shopper nothing about which option to choose."""
    raw = "Amazon Basics Basket Coffee Filters, White, Packaging May Vary, 200 Count"

    result = product_display.display_title(raw)

    assert "Packaging May Vary" not in result
    assert "200 Count" in result
    assert "Coffee Filters" in result


def test_display_title_never_invents_a_pack_size():
    assert "Pack" not in product_display.display_title("Generic AA Batteries Long Lasting")


@pytest.mark.parametrize(
    "title",
    ["", " ", "A" * 400, "Item, , , ,", "(((())))", "Ünïcödé Näme, Wéiß, 3 Päck", "商品, 3個"],
)
def test_display_title_never_raises(title):
    assert isinstance(product_display.display_title(title), str)


# --- facts --------------------------------------------------------------------


def test_facts_show_price_and_arrival_but_not_review_counts():
    """Review counts drive the ranking; printing them beside every option is noise."""
    candidate = _candidate(
        "AA Batteries, 20 Count", price=10.0, price_text="$10.00",
        rating=4.5, reviews=24037, delivery="Mon, Aug 3",
    )

    facts = product_display.candidate_facts(candidate)

    assert "$10.00" in facts
    assert "arrives Mon, Aug 3" in facts
    assert "24,037" not in facts
    assert "reviews" not in facts
    assert "4.5" not in facts


def test_facts_report_a_missing_price_rather_than_omitting_it():
    # The pack count is now always stated too, including when Amazon did not state it:
    # a price with no quantity beside it cannot be compared or trusted.
    facts = product_display.candidate_facts(_candidate("Mystery"))

    assert facts.startswith("price not shown")
    assert "count not stated" in facts


# --- escaping -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [("Tom & Jerry", "Tom &amp; Jerry"), ("<b>hack</b>", "&lt;b&gt;hack&lt;/b&gt;"), (None, "")],
)
def test_untrusted_text_is_escaped_for_html(raw, expected):
    assert product_display.text(raw) == expected


def test_a_product_title_cannot_break_the_message():
    """Amazon titles are attacker-controlled; unescaped angle brackets would break HTML."""
    ranked = RankedCandidates([_candidate("Widget <script>alert(1)</script>", price_text="$1")], ranking.AMAZON_ORDER)

    message = product_display.present_results("widget", ranked, _actions())

    assert "<script>" not in message
    assert "&lt;script&gt;" in message


# --- results vs the user's own list -------------------------------------------


def test_results_and_the_list_are_visually_distinct():
    """A user read their own list as search suggestions, because they looked alike."""
    ranked = RankedCandidates([_candidate("Thing", price=1.0, price_text="$1.00")], ranking.AMAZON_ORDER)
    lines = [CartLine("a", "Thing", 1.0, 1, "$1.00")]

    results = product_display.present_results("thing", ranked, _actions())
    listing = product_display.present_cart(lines, 1.0, _actions())

    assert results.startswith("🔎")
    assert listing.startswith("🧺")
    assert "RESULTS FOR" in results
    assert "YOUR LIST" in listing


def test_no_raw_markdown_reaches_the_user():
    """Telegram sent `**Pick one:**` verbatim because nothing rendered markdown."""
    ranked = RankedCandidates([_candidate("Thing", price=1.0, price_text="$1.00")], ranking.AMAZON_ORDER)

    message = product_display.present_results("thing", ranked, _actions())

    assert "**" not in message
    assert "<b>" in message


def test_results_number_the_products_then_the_actions():
    candidates = [_candidate(f"Item {i}", price=float(i), price_text=f"${i}.00") for i in (1, 2, 3)]
    ranked = RankedCandidates(candidates, ranking.AMAZON_ORDER)

    message = product_display.present_results("things", ranked, _actions())

    assert "1 · <b>Item 1</b>" in message
    assert "3 · <b>Item 3</b>" in message
    assert "4 · Narrow these results" in message
    assert "5 · Start over" in message


def test_empty_results_are_stated_plainly():
    message = product_display.present_results("things", RankedCandidates([], ranking.AMAZON_ORDER), _actions())

    assert "No results" in message
    assert "1 ·" not in message


# --- cart ---------------------------------------------------------------------


def test_cart_shows_quantities_and_a_subtotal():
    lines = [CartLine("a", "Thing", 5.0, 3, "$5.00"), CartLine("b", "Other", 2.0, 1, "$2.00")]

    message = product_display.present_cart(lines, 17.0, _actions())

    assert "×3" in message
    assert "= $15.00" in message
    assert "<b>Subtotal:</b> $17.00" in message


def test_cart_always_says_it_is_not_the_amazon_cart():
    message = product_display.present_cart([CartLine("a", "Thing", 1.0, 1, "$1")], 1.0, _actions())

    # It must not claim the Amazon cart is empty — it said so while four unrelated
    # items were sitting in it. It speaks only about the items on this list.
    assert "have not been sent to your Amazon cart" in message


def test_an_unknown_price_makes_the_subtotal_unavailable():
    message = product_display.present_cart([CartLine("a", "Thing", None, 1)], None, _actions())

    assert "unavailable" in message


def test_empty_cart_message():
    assert "EMPTY" in product_display.present_cart([], None, _actions())


@pytest.mark.parametrize("count", [0, 1, 3, 20])
def test_presentation_scales(count):
    import main

    candidates = [_candidate(f"Product {i}", price=float(i + 1), price_text=f"${i + 1}") for i in range(count)]
    ranked = ranking.rank(candidates, SortPreference.PRICE)

    message = product_display.present_results("things", ranked, _actions())

    assert message.strip()
    for section in main._telegram_sections(message):
        assert len(section) <= main.TELEGRAM_MESSAGE_LIMIT
