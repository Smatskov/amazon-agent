"""Regressions for UAT session 5 — the melatonin and Oral-B transcripts.

Every test here pins a failure the user actually saw in Telegram. The findings were
confirmed against live Amazon result pages with a read-only DOM probe, and the probe
output is quoted in the comments where it changed what the fix had to be.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import agent
import amazon
import flow
import product_display
import ranking
import workflow_store
from menu import MenuAction
from workflow_models import Candidate, PurchaseWorkflow, WorkflowState


USER = 5150


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "m.db", tmp_path / "w.db"


def _run(message, paths, user=USER):
    return asyncio.run(agent.agent_brain(message, paths[0], paths[1], user))


def _melatonin():
    """The exact result set the user was shown, ad included."""
    return [
        amazon.Product("One Medical Membership: Get 24/7 on-demand care for 50+ conditions and more",
                       "$99.00", "https://www.amazon.com/dp/B0CZYRCB2B", prime_eligible=True),
        amazon.Product("Natrol Melatonin 10 mg Fast Dissolve Tablets, Strawberry, 100 Count",
                       "$10.36", "https://www.amazon.com/dp/B01E14X7SM", 4.6, 90000, delivery="Mon, Aug 3", prime_eligible=True),
        amazon.Product("Nature Made Melatonin 10mg Maximum Strength for Sleep Support, 70 Tablets",
                       "$13.99", "https://www.amazon.com/dp/B085V63SWW", 4.7, 40000, delivery="Mon, Aug 3", prime_eligible=True),
        amazon.Product("Natrol Melatonin 10 mg Gummies, 140 Gummies, Strawberry-flavored",
                       "$15.56", "https://www.amazon.com/dp/B08666GMWG", 4.5, 30000, delivery="Mon, Aug 3", prime_eligible=True),
    ]


def _toothbrushes():
    return [
        amazon.Product("Oral-B Pro Clean CrossAction Manual Toothbrush, 6- Pack",
                       "$17.99", "https://www.amazon.com/dp/B01KZ6V00W", 4.6, 300, delivery="Mon, Aug 3", prime_eligible=True),
        amazon.Product("Oral-B Complete Sensitive Toothbrush, 35 Extra Soft - Pack of 4",
                       "$13.46", "https://www.amazon.com/dp/B0BBPB6HV9", 4.4, 200, delivery="Mon, Aug 3", prime_eligible=True),
        amazon.Product("Colgate Extra Clean Full Head Toothbrush, Soft, 6 Pack",
                       "$4.96", "https://www.amazon.com/dp/B00CC6XSSQ", 4.5, 5000, delivery="Mon, Aug 3", prime_eligible=True),
    ]


# --------------------------------------------------------------------------
# Search quality
# --------------------------------------------------------------------------

def test_an_unrelated_placement_is_never_offered_as_an_option(paths, monkeypatch):
    """ISSUE-024. "One Medical Membership, $99.00" was option 1 for "melatonin 10mg".

    Live probing showed it carries a real ASIN, a real price, and no sponsored marker
    of any kind, while the genuine melatonin products *did* carry one. Marker-based ad
    detection would therefore have deleted the good results and kept the placement, so
    relevance to the query is the only usable discriminator.
    """
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_melatonin()))

    reply = _run("melatonin 10mg", paths)

    assert "One Medical" not in reply
    assert "Natrol" in reply
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert all("One Medical" not in c.title for c in workflow.candidates)


def test_results_lead_with_the_cheapest_per_item(paths, monkeypatch):
    """ISSUE-025. Results arrived in Amazon's order, which leads with placements."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))

    _run("toothbrush", paths)

    titles = [c.title for c in workflow_store.get_active_workflow(USER, paths[1]).candidates]
    assert titles[0].startswith("Colgate"), "cheapest per item must be option 1"


def test_nothing_relevant_is_reported_rather_than_hidden():
    """ISSUE-042. Amazon answers any string, so an unparsed sentence still returns
    real listings. Reporting that none are related lets the caller warn instead of
    presenting them as an answer."""
    candidates = [Candidate(candidate_id="a", title="Completely Unrelated Thing", brand=None, price=None)]

    outcome = ranking.relevance(candidates, "melatonin 10mg")

    assert outcome.kept == []
    assert outcome.removed == 1


def test_a_brand_only_title_falls_back_to_the_result_image(monkeypatch):
    """ISSUE-006. Live: every Oral-B card had h2 "Oral-B", an empty anchor, and the
    full name only in the result image's alt text."""

    class _Loc:
        def __init__(self, text=None, alt=None, count=1):
            self._text, self._alt, self._count = text, alt, count

        @property
        def first(self):
            return self

        async def count(self):
            return self._count

        async def text_content(self):
            return self._text

        async def get_attribute(self, name):
            return self._alt if name == "alt" else None

    class _Card:
        def locator(self, selector):
            if selector == "h2":
                return _Loc(text="Oral-B")
            return _Loc(alt="Oral-B Complete Deep Clean Soft Bristles Toothbrush 4 Count")

    title = asyncio.run(amazon._best_title(_Card(), _Loc(text=None)))

    assert title == "Oral-B Complete Deep Clean Soft Bristles Toothbrush 4 Count"


def test_a_replacement_character_in_a_title_is_cleaned():
    """Live: "Oral-B Cavity Defense 123 Black Toothbrush � Medium (Pack of 4)"."""
    assert amazon._clean_title("Oral-B Cavity  Defense � Medium") == "Oral-B Cavity Defense - Medium"


@pytest.mark.parametrize("price,expected", [("$0.00", None), ("$0", None), ("$10.36", "$10.36")])
def test_a_zero_price_is_recorded_as_no_price(price, expected):
    """Live: two melatonin cards extracted "$0.00". Sorting by price would put an
    unbuyable listing first and call it the cheapest."""
    assert amazon._usable_price(price) == expected


def test_the_same_product_is_not_offered_twice():
    """Amazon nests result cards, so one product is reachable under several URLs."""
    assert amazon._asin_from_url("https://www.amazon.com/dp/B01KZ6V00W/ref=sr_1_4?x=1") == "B01KZ6V00W"
    assert amazon._asin_from_url("https://www.amazon.com/gp/other") is None


# --------------------------------------------------------------------------
# Narrowing
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "title,keyword,expected",
    [
        ("Nature's Bounty Melatonin 10mg", "natures bounty", True),
        ("Natures Bounty Melatonin 10mg", "nature's bounty", True),
        # A shared word is not a shared brand: "Nature Made" is not "Nature's Bounty".
        ("Nature Made Melatonin 10mg", "natures bounty", False),
        # Missing dashes must not matter.
        ("Oral-B Pro Clean Toothbrush", "oral b", True),
        # One typo in a long word is tolerated.
        ("Natrol Melatonin 10 mg Tablets", "melatonan", True),
        # Units written apart still match.
        ("Melatonin 10 mg Tablets", "10mg", True),
    ],
)
def test_narrowing_survives_spelling_punctuation_and_spacing(title, keyword, expected):
    """ISSUE-026. "natures bounty" was offered as a valid way to narrow and matched
    nothing, because the filter did a raw substring test on the title."""
    assert ranking.matches_keyword(title, keyword) is expected


@pytest.mark.parametrize(
    "message,low,high",
    [
        ("between 10 and 20 dollars", 10.0, 20.0),
        ("between $10 and $20", 10.0, 20.0),
        ("from 10 to 20 dollars", 10.0, 20.0),
        ("$10-$20", 10.0, 20.0),
        ("10 to 20 dollars", 10.0, 20.0),
        # Written backwards, still a range.
        ("between 20 and 10 dollars", 10.0, 20.0),
    ],
)
def test_a_price_range_is_read_as_a_price_range(message, low, high):
    """ISSUE-040. "between 10 and 20 dollars" matched no max-price phrase, so
    "between" and "dollars" survived as leftover words and became a keyword the title
    had to contain — which filtered out every result including the $10.97 one."""
    constraints = ranking.parse_constraint(message)

    assert constraints["min_price"] == low
    assert constraints["max_price"] == high
    assert "keyword" not in constraints, "money words must never become a keyword"


def test_a_price_range_keeps_only_products_inside_it():
    candidates = [
        Candidate(candidate_id="a", title="Dove Body Wash A", brand=None, price=9.50),
        Candidate(candidate_id="b", title="Dove Body Wash B", brand=None, price=10.97),
        Candidate(candidate_id="c", title="Dove Body Wash C", brand=None, price=30.20),
    ]

    kept = ranking.apply_constraints(candidates, ranking.parse_constraint("between 10 and 20 dollars")).kept

    assert [c.candidate_id for c in kept] == ["b"]


@pytest.mark.parametrize(
    "message,expected",
    [("over $10", {"min_price": 10.0}), ("at least 15", {"min_price": 15.0}),
     ("up to 25", {"max_price": 25.0})],
)
def test_open_ended_bounds_are_read(message, expected):
    constraints = ranking.parse_constraint(message)
    for key, value in expected.items():
        assert constraints[key] == value


def test_a_budget_is_sent_to_amazon_not_just_applied_locally(paths, monkeypatch):
    """ISSUE-040. "under 10" reported nothing matched while Amazon had six Dove body
    washes from $5.47 — because the re-search asked for the same unfiltered page."""
    search = AsyncMock(return_value=[
        amazon.Product("Dove Body Wash Deep Moisture, 30.6 oz", "$10.97",
                       "https://www.amazon.com/dp/B00MEDOY2G", delivery="Mon, Aug 3", prime_eligible=True),
    ])
    monkeypatch.setattr(agent.amazon, "search_products", search)

    _run("dove body wash", paths)
    _run("2", paths)  # Narrow (only one product, so the action sits at 2)
    search.return_value = [
        amazon.Product("Dove Men+Care Micro Moisture Body and Face Wash, 13.5 fl oz", "$5.47",
                       "https://www.amazon.com/dp/B07CV1234X", delivery="Mon, Aug 3", prime_eligible=True),
    ]
    reply = _run("under 10", paths)

    assert search.await_args.kwargs["max_price"] == 10.0
    assert "$5.47" in reply


def test_amazon_price_bounds_reach_the_url():
    """Live-verified: low-price/high-price are honoured. rh=p_36 and s=price-asc-rank
    both returned "Sorry! Something went wrong!" and are deliberately not used."""
    url = amazon._search_url("dove body wash", 20.0, 10.0)

    assert "low-price=10" in url and "high-price=20" in url
    assert "rh=" not in url and "s=price-asc-rank" not in url


def test_narrowing_by_brand_asks_amazon_for_that_brand(paths, monkeypatch):
    """ISSUE-026. Re-filtering results already retrieved can only ever remove; a brand
    that was not already in the top five could never be found."""
    search = AsyncMock(return_value=_melatonin())
    monkeypatch.setattr(agent.amazon, "search_products", search)

    _run("melatonin 10mg", paths)
    _run("4", paths)  # Narrow these results
    search.return_value = [
        amazon.Product("Nature's Bounty Melatonin 10mg, 180 Tablets", "$9.49",
                       "https://www.amazon.com/dp/NB1", 4.6, 5000, delivery="Mon, Aug 3", prime_eligible=True),
    ]
    reply = _run("natures bounty", paths)

    assert "Nature's Bounty" in reply
    assert search.await_args.args[0] == "natures bounty melatonin 10mg"


def test_a_narrowing_that_finds_nothing_shows_no_products(paths, monkeypatch):
    """ISSUE-039. Reprinting the results that just failed the filter under a "nothing
    matched" heading read as a successful narrowing, and invited the user to pick one
    of the very items they had excluded."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_melatonin()))

    _run("melatonin 10mg", paths)
    _run("4", paths)  # Narrow these results
    reply = _run("zzzzqqqq", paths)

    assert "couldn't find any products matching" in reply
    assert "Natrol" not in reply, "results that failed the filter must not be shown"
    assert "Show the results I had before" in reply


# --------------------------------------------------------------------------
# Routing: the answer to a question must not be matched against old results
# --------------------------------------------------------------------------

def test_answering_what_should_i_look_for_starts_a_new_search(paths, monkeypatch):
    """ISSUE-027. The agent asked "What should I look for?", the user answered with a
    product, and the answer was matched against the previous search's candidates —
    producing "More than one option matches that description"."""
    search = AsyncMock(return_value=_toothbrushes())
    monkeypatch.setattr(agent.amazon, "search_products", search)

    _run("oral b toothbrush 4 pack", paths)
    assert "What should I look for?" in _run("5", paths)  # Search for something else

    reply = _run("oral b branded toothpaste 4 pack", paths)

    assert "More than one option" not in reply
    assert search.await_args.args[0] == "oral b branded toothpaste 4 pack"


def test_a_number_still_works_after_asking_to_search_for_something_else(paths, monkeypatch):
    """ISSUE-028. The results were still on screen but the menu had been cleared, so
    "3" was answered with "Which product should I search for on Amazon?"."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))

    _run("toothbrush", paths)
    _run("5", paths)  # Search for something else
    reply = _run("3", paths)

    assert "Which product should I search for" not in reply
    assert "Added" in reply


def test_a_cart_menu_is_not_reused_after_moving_on(paths, monkeypatch):
    """The mirror of the test above: keeping a *cart* menu alive would make "1" mean
    "Check out" long after the user asked to shop for something else."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))

    _run("toothbrush", paths)
    _run("1", paths)          # add an item, leaving the cart menu pending
    _run("2", paths)          # Add something else
    workflow = workflow_store.get_active_workflow(USER, paths[1])

    assert not any(o.action is MenuAction.CHECKOUT for o in workflow.pending_menu)


def test_naming_a_product_does_not_silently_add_a_previous_result(paths, monkeypatch):
    """ISSUE-029. "oral-B toothbrushes 6 pack" was intended as a search. It matched a
    stale candidate on four words and was added to the list without Amazon ever being
    asked for it."""
    search = AsyncMock(return_value=_toothbrushes())
    monkeypatch.setattr(agent.amazon, "search_products", search)

    _run("toothbrush", paths)
    reply = _run("oral-B toothbrushes 6 pack", paths)

    assert search.await_args.args[0] == "oral-B toothbrushes 6 pack"
    assert "RESULTS FOR" in reply


def test_a_short_reference_still_selects_what_is_on_screen(paths, monkeypatch):
    """The counterweight: pointing at something shown must keep working."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))

    _run("toothbrush", paths)
    reply = _run("the colgate", paths)

    assert "Added" in reply
    assert "Colgate" in reply


def test_an_ambiguous_reference_is_asked_with_the_menu_attached(paths, monkeypatch):
    """ISSUE-030. "More than one option matches that description. Which option do you
    mean?" arrived with nothing to choose from."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))
    _run("toothbrush", paths)
    workflow = workflow_store.get_active_workflow(USER, paths[1])

    reply = agent._with_menu(workflow, "More than one option matches that description.")

    assert "Pick a number:" in reply
    assert "Colgate" in reply


# --------------------------------------------------------------------------
# Display
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Titles must stay readable once they are no longer brand-only
# --------------------------------------------------------------------------

def test_a_shortened_title_never_ends_on_a_dangling_word():
    """ISSUE-036. "…Deep Moisture for…" reads as a damaged name, not a short one."""
    shortened = product_display.display_title(
        "Dove Body Wash with Pump 3 Count Deep Moisture for 24hr Lotion-Soft Skin Cleanser"
    )

    assert not shortened.rstrip("…").rstrip().endswith(" for")
    assert "3 Count" in shortened


def test_a_size_survives_a_pack_count_in_the_head():
    """ISSUE-036. "Dove Body Wash 2-Pack" dropped "15.2 Oz Ea", which is the only
    thing separating it from another 2-pack."""
    shortened = product_display.display_title(
        "Dove Body Wash 2-Pack – Deeply Nourishing for Softer, Smoother Skin, 15.2 Oz Ea"
    )

    assert "2-Pack" in shortened
    assert "15.2 Oz" in shortened


def test_five_dove_listings_are_told_apart(paths, monkeypatch):
    """ISSUE-037. Five distinct ASINs all displayed as "Dove", so they read as the
    same product repeated. Live-verified: the titles differ; only extraction failed."""
    dove = [
        amazon.Product("Dove 24hr Lotion Body Wash Deep Moisture, 30.6 oz | Moisturizing body wash",
                       "$10.97", "https://www.amazon.com/dp/B00MEDOY2G", delivery="Mon, Aug 3", prime_eligible=True),
        amazon.Product("Dove 24hr Lotion Body Wash Sensitive Skin with Pump, 30.6 oz | Hypoallergenic",
                       "$10.97", "https://www.amazon.com/dp/B00SK71SAG", delivery="Mon, Aug 3", prime_eligible=True),
        amazon.Product("Dove 24hr Lotion Antibacterial Body Wash with Pump, 30.6 oz | Eliminates 99%",
                       "$10.97", "https://www.amazon.com/dp/B09DDD6YGJ", delivery="Mon, Aug 3", prime_eligible=True),
    ]
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=dove))

    reply = _run("dove body wash", paths)

    assert "Deep Moisture" in reply
    assert "Sensitive Skin" in reply
    assert "Antibacterial" in reply


def test_the_narrow_option_says_what_it_accepts(paths, monkeypatch):
    """ISSUE-038. "Narrow these results" then asked what to narrow by, spending a turn
    to deliver information the label could have carried."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))

    reply = _run("toothbrush", paths)

    assert "brand" in reply and "budget" in reply


def test_the_add_to_cart_control_is_polled_across_the_redirect():
    """ISSUE-033. Live-verified: product pages redirect to a variation URL
    (`/dp/B00CC6XSSQ` becomes `?th=1`) *after* DOMContentLoaded, and a locator wait
    started before that navigation times out. The button appears 1.5-2.5s in, so the
    page is settled and the button polled rather than waited on once."""
    calls = {"count": 0}

    class _Button:
        @property
        def first(self):
            return self

        async def count(self):
            calls["count"] += 1
            return 0 if calls["count"] < 4 else 1   # appears only after the redirect

        async def get_attribute(self, name):
            return amazon.ADD_TO_CART_BUTTON_ID if name == "id" else None

    class _Page:
        url = "https://www.amazon.com/dp/B00CC6XSSQ?th=1"

        def locator(self, selector):
            return _Button()

        async def wait_for_load_state(self, *args, **kwargs):
            return None

        async def wait_for_timeout(self, ms):
            return None

    button = asyncio.run(amazon._add_to_cart_button(_Page()))

    assert button is not None
    assert calls["count"] >= 4, "must keep looking rather than give up on the first check"


def test_a_page_that_truly_has_no_control_still_fails(monkeypatch):
    """The wait must not turn a genuine absence into a hang or a false success."""
    monkeypatch.setattr(amazon, "ADD_TO_CART_WAIT_MS", 300)

    class _Button:
        @property
        def first(self):
            return self

        async def count(self):
            return 0

    class _Page:
        url = "https://www.amazon.com/dp/BNONE"

        def locator(self, selector):
            return _Button()

        async def wait_for_load_state(self, *args, **kwargs):
            return None

        async def wait_for_timeout(self, ms):
            return None

    with pytest.raises(amazon.AmazonCartUnavailable, match="No Add to Cart control"):
        asyncio.run(amazon._add_to_cart_button(_Page()))


# --------------------------------------------------------------------------
# The real Amazon cart is not the same thing as the agent's list
# --------------------------------------------------------------------------

def _confirm_flow(paths, monkeypatch, cart_contents):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))
    monkeypatch.setattr(
        agent.amazon, "add_many_to_cart",
        AsyncMock(return_value=[amazon.CartWriteResult("https://www.amazon.com/dp/B00CC6XSSQ", 1, True)]),
    )
    monkeypatch.setattr(agent.amazon, "read_cart", AsyncMock(return_value=cart_contents))
    _run("toothbrush", paths)
    _run("1", paths)          # add the Colgate
    return _run("1", paths)   # Check out pushes to the Amazon cart


def test_an_item_the_agent_did_not_add_is_reported_at_confirmation(paths, monkeypatch):
    """ISSUE-032. The confirmed summary described the agent's list, but the order the
    user would place is the whole Amazon cart. A pack of coffee filters left there from
    an earlier session rode along unmentioned."""
    reply = _confirm_flow(paths, monkeypatch, [
        amazon.Product("Colgate Extra Clean Full Head Toothbrush, Soft, 6 Pack", "$4.96",
                       "https://www.amazon.com/dp/B00CC6XSSQ", prime_eligible=True),
        amazon.Product("Amazon Basics Basket Coffee Filters, 200 Count", "$1.98",
                       "https://www.amazon.com/dp/B00LM4M5FS", prime_eligible=True),
    ])

    assert "ALREADY IN YOUR CART" in reply
    assert "Coffee Filters" in reply


def test_a_cart_holding_only_our_items_is_not_flagged(paths, monkeypatch):
    reply = _confirm_flow(paths, monkeypatch, [
        amazon.Product("Colgate Extra Clean Full Head Toothbrush, Soft, 6 Pack", "$4.96",
                       "https://www.amazon.com/dp/B00CC6XSSQ", prime_eligible=True),
    ])

    assert "ALREADY IN YOUR CART" not in reply


def test_a_cart_read_failure_never_breaks_the_confirmation(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))
    monkeypatch.setattr(
        agent.amazon, "add_many_to_cart",
        AsyncMock(return_value=[amazon.CartWriteResult("https://www.amazon.com/dp/B00CC6XSSQ", 1, True)]),
    )
    monkeypatch.setattr(agent.amazon, "read_cart", AsyncMock(side_effect=RuntimeError("offline")))

    _run("toothbrush", paths)
    _run("1", paths)          # add
    reply = _run("1", paths)  # check out — this is what the failing read must not break

    assert "IN YOUR AMAZON CART" in reply


def test_the_remove_menu_shows_what_each_item_costs(paths, monkeypatch):
    """ISSUE-031. Choosing what to drop from a list is a money decision."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))

    _run("toothbrush", paths)
    _run("1", paths)
    reply = _run("4", paths)  # Remove an item (3 is now "Change a quantity")

    assert "$4.96" in reply


# --------------------------------------------------------------------------
# Variation listings and quantity
# --------------------------------------------------------------------------

def _variants():
    return [
        amazon.Variant("B0FTHJCPFQ", "Swagger · 3.8 Ounce (Pack of 1)",
                       "https://www.amazon.com/dp/B0FTHJCPFQ"),
        amazon.Variant("B09NPNPKGS", "Swagger · 3.8 Ounce (Pack of 3)",
                       "https://www.amazon.com/dp/B09NPNPKGS"),
    ]


def test_the_variation_map_is_read_from_amazons_own_inline_data():
    """Live-verified shape: Amazon ships every child ASIN with its dimension values."""
    html = (
        'x "dimensionValuesDisplayData":{"B0FTHJCPFQ":["Swagger","3.8 Ounce (Pack of 1)"],'
        '"B09NPNPKGS":["Swagger","3.8 Ounce (Pack of 3)"]}, "y"'
    )

    variants = amazon._variants_from_html(html)

    assert [v.asin for v in variants] == ["B0FTHJCPFQ", "B09NPNPKGS"]
    assert variants[0].label == "Swagger · 3.8 Ounce (Pack of 1)"
    assert variants[0].url.endswith("/dp/B0FTHJCPFQ")


def test_a_page_with_no_variation_map_yields_nothing():
    assert amazon._variants_from_html("<html>no twister here</html>") == []


def test_choosing_a_variation_listing_asks_which_version(paths, monkeypatch):
    """ISSUE-043. A variation parent has no fixed identity — scent, size and pack are
    chosen on the product page — so adding one either failed outright or was ambiguous
    about what would arrive."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))
    monkeypatch.setattr(agent.amazon, "read_variants", AsyncMock(return_value=_variants()))

    _run("toothbrush", paths)
    reply = _run("1", paths)

    assert "comes in 2 versions" in reply
    assert "Pack of 3" in reply
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert workflow.cart == [], "nothing may be added before the version is chosen"


def test_choosing_a_version_adds_that_exact_child_asin(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))
    monkeypatch.setattr(agent.amazon, "read_variants", AsyncMock(return_value=_variants()))
    monkeypatch.setattr(agent.amazon, "read_product", AsyncMock(return_value=amazon.Product(
        "Old Spice High Endurance, Swagger, 3.8 oz, Pack of 3", "$12.97",
        "https://www.amazon.com/dp/B09NPNPKGS", prime_eligible=True)))

    _run("toothbrush", paths)
    _run("1", paths)
    # Sorting puts the larger pack first, so the Pack of 3 is option 1 (ADR-063).
    reply = _run("1", paths)

    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert [line.candidate_id for line in workflow.cart] == ["amazon-B09NPNPKGS"]
    assert workflow.cart[0].price_text == "$12.97"
    assert "Pack of 3" in reply


def test_a_listing_with_one_version_is_added_without_asking(paths, monkeypatch):
    """The extra step must only appear where there is a real choice to make."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))
    monkeypatch.setattr(agent.amazon, "read_variants", AsyncMock(return_value=_variants()[:1]))

    _run("toothbrush", paths)
    reply = _run("1", paths)

    assert "Added" in reply
    assert len(workflow_store.get_active_workflow(USER, paths[1]).cart) == 1


def test_a_failed_variant_read_still_adds_the_item(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))
    monkeypatch.setattr(agent.amazon, "read_variants", AsyncMock(side_effect=RuntimeError("offline")))

    _run("toothbrush", paths)
    reply = _run("1", paths)

    assert "Added" in reply


def test_quantity_can_be_changed_again(paths, monkeypatch):
    """ISSUE-023. Quantity became unreachable when the semantic path was removed, so
    adding two of anything was impossible."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))
    monkeypatch.setattr(agent.amazon, "read_variants", AsyncMock(return_value=[]))

    _run("toothbrush", paths)
    _run("1", paths)
    _run("3", paths)          # Change a quantity
    _run("1", paths)          # the only item
    reply = _run("3", paths)  # quantity 3

    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert workflow.cart[0].quantity == 3
    assert workflow.confirmed_token is None, "a changed quantity invalidates approval"
    assert "3" in reply


# --------------------------------------------------------------------------
# Placing a real order: the two outcomes, and the controls in front of them
# --------------------------------------------------------------------------

def _at_the_order_screen(paths, monkeypatch):
    """Drive to the screen where "1" is Place the order."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))
    monkeypatch.setattr(agent.amazon, "read_variants", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        agent.amazon, "add_many_to_cart",
        AsyncMock(return_value=[amazon.CartWriteResult("https://www.amazon.com/dp/B00CC6XSSQ", 1, True)]),
    )
    monkeypatch.setattr(agent.amazon, "read_cart", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent.amazon, "read_destination",
                        AsyncMock(return_value=amazon.Destination("tanay Tx 75160", "Visa ending 6250")))
    _run("toothbrush", paths)
    _run("1", paths)   # add
    _run("1", paths)   # check out


def test_a_placed_order_reports_the_order_number_and_clears_the_list(paths, monkeypatch):
    _at_the_order_screen(paths, monkeypatch)
    monkeypatch.setattr(agent.amazon, "place_order", AsyncMock(return_value=amazon.OrderResult(
        True, order_id="112-3456789-1234567",
        order_url="https://www.amazon.com/gp/css/order-history")))

    reply = _run("1", paths)

    assert "ORDER PLACED" in reply
    assert "112-3456789-1234567" in reply
    assert "order-history" in reply
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert workflow is None or workflow.cart == [], "a placed order clears the list"


def test_a_declined_card_says_so_and_keeps_the_list(paths, monkeypatch):
    """The list must survive so the user can fix the card and try again."""
    _at_the_order_screen(paths, monkeypatch)
    monkeypatch.setattr(agent.amazon, "place_order", AsyncMock(return_value=amazon.OrderResult(
        False, declined=True, detail="Amazon rejected the payment method. Nothing was ordered.")))

    reply = _run("1", paths)

    assert "PAYMENT DECLINED" in reply
    assert "Your list is untouched" in reply
    assert "Update your payment method" in reply
    assert len(workflow_store.get_workflow(USER, paths[1]).cart) == 1


def test_the_sign_in_wall_is_reported_as_the_users_step(paths, monkeypatch):
    """Live-verified: Amazon redirects checkout to /ap/signin with max_auth_age=900.
    The agent never authenticates, so this is handed back rather than worked around."""
    _at_the_order_screen(paths, monkeypatch)
    monkeypatch.setattr(agent.amazon, "place_order", AsyncMock(return_value=amazon.OrderResult(
        False, needs_sign_in=True,
        detail="Amazon wants you to sign in again before it will accept an order.")))

    reply = _run("1", paths)

    assert "SIGN IN" in reply
    assert "never enter passwords" in reply
    assert "Your list is untouched" in reply
    assert len(workflow_store.get_workflow(USER, paths[1]).cart) == 1


def test_a_failed_order_offers_exactly_the_four_recovery_choices(paths, monkeypatch):
    _at_the_order_screen(paths, monkeypatch)
    monkeypatch.setattr(agent.amazon, "place_order",
                        AsyncMock(return_value=amazon.OrderResult(False, detail="something broke")))

    _run("1", paths)

    actions = [o.action for o in workflow_store.get_workflow(USER, paths[1]).pending_menu]
    assert actions == [MenuAction.VIEW_LIST, MenuAction.KEEP_SHOPPING,
                       MenuAction.REMOVE, MenuAction.CANCEL]


def test_a_placed_order_offers_only_shopping_again(paths, monkeypatch):
    _at_the_order_screen(paths, monkeypatch)
    monkeypatch.setattr(agent.amazon, "place_order",
                        AsyncMock(return_value=amazon.OrderResult(True, order_id="111-2222222-3333333")))

    _run("1", paths)

    workflow = workflow_store.get_workflow(USER, paths[1])
    assert [o.action for o in workflow.pending_menu] == [MenuAction.KEEP_SHOPPING]


def test_an_unconfirmed_outcome_is_never_reported_as_success(paths, monkeypatch):
    """Amazon showing no confirmation is not the same as an order failing, and it is
    certainly not success. It has to be said plainly so the user checks."""
    _at_the_order_screen(paths, monkeypatch)
    monkeypatch.setattr(agent.amazon, "place_order", AsyncMock(return_value=amazon.OrderResult(
        False, detail="Amazon did not show a confirmation, so I cannot tell you the order "
                      "went through. Check your Amazon orders before trying again.")))

    reply = _run("1", paths)

    assert "cannot tell you the order went through" in reply
    assert "ORDER PLACED" not in reply
    assert len(workflow_store.get_workflow(USER, paths[1]).cart) == 1


# --------------------------------------------------------------------------
# Variant ordering (the picker only — the results list must not move)
# --------------------------------------------------------------------------

def _iphone_variants():
    return [[a, l, ""] for a, l in [
        ("A1", "Forest Green · For Iphone 17 Pro"), ("A2", "Blue Green · For Iphone Se/8"),
        ("A3", "Cangling Green · For Iphone XR"), ("A4", "Desert Gold · iPhone 12/12 Pro"),
        ("A6", "Red · For Iphone 16 Plus"), ("A8", "Light Pink · iPhone 15 Pro Max"),
        ("A9", "Lavender Gray · For Iphone 15"), ("A11", "Cangling Green · For Iphone 16 Pro Max"),
    ]]


def test_variants_run_newest_model_then_largest_size_then_colour():
    """ISSUE-053. Twelve versions arrived in Amazon's arbitrary order, so a case for
    an iPhone 8 sat above the one for the phone the user actually has."""
    ordered = ranking.sort_variants(_iphone_variants())

    models = [row[1].split("·")[-1].strip() for row in ordered]
    assert models[0] == "For Iphone 17 Pro"
    assert models.index("For Iphone 16 Pro Max") < models.index("For Iphone 16 Plus")
    assert models.index("iPhone 15 Pro Max") > models.index("For Iphone 16 Plus")
    assert models[-1] == "For Iphone XR", "a model with no number is oldest"


def test_the_version_the_result_described_is_pinned_first():
    ordered = ranking.sort_variants(_iphone_variants(), "A6")

    assert ordered[0][0] == "A6", "the version the user actually looked at leads"
    # Everything after it is still in newest-first order.
    assert ordered[1][1].endswith("For Iphone 17 Pro")


def test_the_pinned_version_is_labelled_in_the_menu():
    workflow = PurchaseWorkflow.new(1, "case", "case")
    workflow.pending_variants = ranking.sort_variants(_iphone_variants(), "A6")
    workflow.selected_candidate_id = "amazon-A6"

    labels = [o.label for o in flow.variant_menu(workflow)]

    assert labels[0].endswith("this is the one shown in the results")
    assert sum("shown in the results" in label for label in labels) == 1


def test_variant_sorting_never_touches_the_results_ordering(paths, monkeypatch):
    """The five search results are different products competing on price and must keep
    their cheapest-per-item order. Only one product's versions are re-sorted."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))
    monkeypatch.setattr(agent.amazon, "read_variants", AsyncMock(return_value=[]))

    _run("toothbrush", paths)

    titles = [c.title for c in workflow_store.get_active_workflow(USER, paths[1]).candidates]
    assert titles[0].startswith("Colgate"), "cheapest per item still leads the results"
    assert titles[-1].startswith("Oral-B Complete Sensitive")


def test_pack_variants_sort_largest_first():
    """The same rule has to behave for a non-phone product."""
    rows = [["B1", "Swagger · 3.8 Ounce (Pack of 1)", ""],
            ["B2", "Swagger · 3.8 Ounce (Pack of 3)", ""],
            ["B3", "Aqua Reef · 3.8 Ounce (Pack of 3)", ""]]

    ordered = ranking.sort_variants(rows)

    assert [row[0] for row in ordered] == ["B3", "B2", "B1"], (
        "packs of 3 lead the pack of 1, and colour breaks the tie between them"
    )


def test_a_stray_amazon_cart_item_can_be_removed(paths, monkeypatch):
    """ISSUE-054. The warning named items the order would buy but offered no way to
    drop them, leaving the user with a problem and no control."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_toothbrushes()))
    monkeypatch.setattr(agent.amazon, "read_variants", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent.amazon, "add_many_to_cart", AsyncMock(
        return_value=[amazon.CartWriteResult("https://www.amazon.com/dp/B00CC6XSSQ", 1, True)]))
    monkeypatch.setattr(agent.amazon, "read_destination",
                        AsyncMock(return_value=amazon.Destination(None, None)))
    monkeypatch.setattr(agent.amazon, "read_cart", AsyncMock(return_value=[
        amazon.Product("Colgate Extra Clean Full Head Toothbrush, Soft, 6 Pack", "$4.96",
                       "https://www.amazon.com/dp/B00CC6XSSQ", prime_eligible=True),
        amazon.Product("SUPFINE Magnetic for iPhone 17 Pro Case Forest Green", "$14.99",
                       "https://www.amazon.com/dp/BCASE001", prime_eligible=True),
    ]))
    remove = AsyncMock(return_value=1)
    monkeypatch.setattr(agent.amazon, "remove_from_cart", remove)

    _run("toothbrush", paths)
    _run("1", paths)          # add
    _run("1", paths)          # check out
    reply = _run("2", paths)  # Remove an item

    assert "already in your Amazon cart" in reply, "the stray item must be offered"
    assert "SUPFINE" in reply

    workflow = workflow_store.get_active_workflow(USER, paths[1])
    stray = next(o for o in workflow.pending_menu
                 if o.payload and o.payload.startswith("amazon-cart:"))
    after = _run(str(workflow.pending_menu.index(stray) + 1), paths)

    remove.assert_awaited_once_with("BCASE001")
    assert "Removed" in after


def test_removing_a_stray_item_reports_a_failure_honestly(paths, monkeypatch):
    workflow = PurchaseWorkflow.new(USER, "x", "x")
    workflow.amazon_cart = [["Some Item", "$9.99", False, "BSTRAY01"]]
    monkeypatch.setattr(agent.amazon, "remove_from_cart",
                        AsyncMock(side_effect=RuntimeError("not in the cart")))

    reply = asyncio.run(agent._remove_from_amazon_cart(workflow, "BSTRAY01", paths[1]))

    assert "could not remove" in reply
    assert workflow.amazon_cart, "a failed removal must not pretend the item is gone"


# --------------------------------------------------------------------------
# The checkout pipeline: what may be clicked, and what may never be
# --------------------------------------------------------------------------

def test_a_paid_amazon_offer_is_never_accepted():
    """ISSUE-055. Amazon injects a Prime free-trial offer mid-checkout for non-members.
    Its prominent button enrols the user in a $14.99/month subscription; only the
    decline may be clicked."""
    for accept in ["Get FREE Prime Delivery with Prime", "Start your free trial",
                   "Sign up for Prime", "Join Prime", "Buy now"]:
        assert amazon.NEVER_CLICK.search(accept), f"{accept!r} must be refused"
    for safe in ["No thanks", "Use this payment method", "Place your order"]:
        assert not amazon.NEVER_CLICK.search(safe), f"{safe!r} must stay clickable"


def test_the_decline_control_is_matched_exactly():
    assert amazon.PRIME_DECLINE_TEXT.match("No thanks")
    assert amazon.PRIME_DECLINE_TEXT.match("  No thanks  ")
    assert not amazon.PRIME_DECLINE_TEXT.match("No thanks, get Prime later")


def test_every_order_selector_targets_the_clickable_input():
    """Amazon lays an <input type=submit> over a <span> label; clicking the label
    times out with "input intercepts pointer events"."""
    for selector in (amazon.PLACE_ORDER_SELECTOR, amazon.CHECKOUT_CONTINUE_SELECTOR):
        for part in selector.split(","):
            part = part.strip()
            assert "input" in part or part.startswith("#"), f"{part!r} is not an input"


def test_the_pipeline_gives_up_rather_than_clicking_something_unknown(monkeypatch):
    """If the order control never appears, nothing else may be pressed in its place."""
    assert amazon.MAX_CHECKOUT_STEPS <= 8, "a bounded walk, not an open-ended one"


def test_card_verification_is_reported_as_the_users_step(paths, monkeypatch):
    """ISSUE-056. Live: Amazon blocked the payment step behind "Verify your card —
    please re-enter your card number". Entering a card number is never done here, so
    the wall is handed back rather than worked around."""
    _at_the_order_screen(paths, monkeypatch)
    monkeypatch.setattr(agent.amazon, "place_order", AsyncMock(return_value=amazon.OrderResult(
        False, needs_card_verification=True,
        detail="Amazon wants your card verified before it will accept this order.")))

    reply = _run("1", paths)

    assert "CARD VERIFIED" in reply
    assert "never type card numbers" in reply
    assert "one-time step per card" in reply
    assert "Your list is untouched" in reply
    assert len(workflow_store.get_workflow(USER, paths[1]).cart) == 1


@pytest.mark.parametrize("text", [
    "Verify your card", "Please re-enter your card number to verify this is an authorized use",
    "Verify card",
])
def test_the_card_verification_wall_is_recognised(text):
    assert amazon.CARD_VERIFICATION.search(text)


def test_a_blocked_checkout_step_does_not_raise_a_raw_timeout():
    """Live: a gated step kept its button in the DOM, so an unbounded click waited the
    full 30s default and put a Playwright stack trace into the audit log."""
    assert amazon.CHECKOUT_CLICK_TIMEOUT_MS <= 10_000


# --------------------------------------------------------------------------
# The real order control, mapped live on Amazon's review page
# --------------------------------------------------------------------------

def test_the_enabled_order_control_is_named_before_the_generic_one():
    """Live: the review page renders several inputs with id="placeOrder", one of them
    a disabled twin. Matching the id alone and taking the first hit can resolve to a
    control that never submits."""
    parts = [p.strip() for p in amazon.PLACE_ORDER_SELECTOR.split(",")]

    assert parts[0].startswith('input[data-csa-c-slot-id="checkout-place-your-order-button"]')
    assert 'data-testid="SPC_selectPlaceOrder"' in parts[0]
    assert amazon.DISABLED_ORDER_TESTID not in amazon.PLACE_ORDER_SELECTOR


def test_a_disabled_order_control_is_never_returned():
    class _Control:
        def __init__(self, testid, enabled):
            self._testid, self._enabled = testid, enabled

        async def get_attribute(self, name):
            return self._testid if name == "data-testid" else None

        async def is_enabled(self):
            return self._enabled

    class _Locator:
        def __init__(self, controls):
            self._controls = controls

        async def count(self):
            return len(self._controls)

        def nth(self, index):
            return self._controls[index]

    class _Page:
        def __init__(self, controls):
            self._controls = controls

        def locator(self, selector):
            return _Locator(self._controls)

    disabled = _Control(amazon.DISABLED_ORDER_TESTID, True)
    not_enabled = _Control("SPC_selectPlaceOrder", False)
    real = _Control("SPC_selectPlaceOrder", True)

    assert asyncio.run(amazon._enabled_order_button(_Page([disabled, not_enabled, real]))) is real
    assert asyncio.run(amazon._enabled_order_button(_Page([disabled, not_enabled]))) is None
    assert asyncio.run(amazon._enabled_order_button(_Page([]))) is None


# --------------------------------------------------------------------------
# Prime eligibility is a hard rule on everything the agent suggests
# --------------------------------------------------------------------------

def _prime(flag):
    return Candidate(candidate_id=f"c{flag}", title="Coffee Filters 200 Count", brand=None,
                     price=1.98, prime_eligible=flag)


def test_only_prime_eligible_results_are_ever_suggested():
    """ISSUE-057. A product that cannot ship free is never worth suggesting."""
    outcome = ranking.prime_only([_prime(True), _prime(False), _prime(None)])

    assert [c.prime_eligible for c in outcome.kept] == [True]
    assert outcome.removed == 2
    assert "not Prime eligible" in outcome.reasons


def test_prime_eligibility_is_never_inferred():
    """Absent evidence is not evidence: a card with no badge is dropped, not guessed."""
    assert ranking.prime_only([_prime(None)]).kept == []


def test_a_search_with_no_prime_results_suggests_nothing(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=[
        amazon.Product("Coffee Filters, 600 Pack", "$9.49",
                       "https://www.amazon.com/dp/BNOPRIME", delivery="Mon, Aug 3"),
    ]))

    reply = _run("coffee filters", paths)

    assert "Prime eligible" in reply
    assert "BNOPRIME" not in reply
    assert workflow_store.get_active_workflow(USER, paths[1]) is None


def test_a_membership_upsell_does_not_strip_a_products_prime_badge():
    """The badge is a fact about the product; a "Join Prime" advert beside it is a
    fact about the account. Conflating them dropped genuinely eligible results."""
    html = '<i class="a-icon a-icon-prime"></i><span>Join Prime to get FREE delivery</span>'

    _, _, _, prime = amazon._result_metadata_from_html(html)

    assert prime is True


def test_the_free_delivery_matcher_ignores_a_priced_option():
    assert amazon.FREE_DELIVERY.search("FREE Delivery Wednesday, August 5")
    assert amazon.PAID_DELIVERY.search("$6.99 - Tuesday, August 4")
    assert not amazon.PAID_DELIVERY.search("FREE Delivery Wednesday, August 5")


# --------------------------------------------------------------------------
# The "Need anything else?" add-on carousel (captured live, 2026-08-02)
# --------------------------------------------------------------------------

def test_the_add_on_carousel_is_passed_by_continue_to_checkout():
    """ISSUE-058. A live order attempt stalled on Amazon's "Need anything else?"
    interstitial at /checkout/byg/. The way past it is the "Continue to checkout"
    button; the pipeline recognised nothing on the page and gave up."""
    assert amazon.CHECKOUT_ADVANCE_TEXT.match("Continue to checkout")
    assert amazon.CHECKOUT_ADVANCE_TEXT.match("Proceed to checkout")
    assert amazon.CHECKOUT_ADVANCE_TEXT.match("No thanks")


@pytest.mark.parametrize("label", [
    "Add to cart", "Add both to cart", "Buy now", "Subscribe & Save",
    "Get FREE Prime Delivery with Prime", "Start your free trial",
])
def test_nothing_that_adds_a_product_or_a_subscription_is_ever_clicked(label):
    """That interstitial is a wall of add-on carousels: every suggested product has
    its own Add to cart button. Pressing one would put a product the user never asked
    for into the order they are about to place."""
    assert amazon.NEVER_CLICK.search(label), f"{label!r} must be refused"
    assert not amazon.CHECKOUT_ADVANCE_TEXT.match(label), f"{label!r} must not advance"


def test_an_advance_label_is_matched_whole_not_by_substring():
    """"Continue to checkout" sits inches from "Add to cart" on the same page."""
    assert not amazon.CHECKOUT_ADVANCE_TEXT.match("Continue shopping and add to cart")
    assert not amazon.CHECKOUT_ADVANCE_TEXT.match("No thanks, add this instead")
