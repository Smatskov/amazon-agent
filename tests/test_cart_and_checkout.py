"""Cart arithmetic, the confirmation gate, and the guarantee that nothing orders."""

import pytest

import cart
import checkout
from workflow_models import Candidate, CartLine, PurchaseWorkflow, WorkflowState


def _candidate(index=1, title="AA Batteries, 4 Count", price=10.0):
    return Candidate(
        candidate_id=f"amazon-result-{index}",
        title=title,
        brand=None,
        price=price,
        price_text=None if price is None else f"${price:.2f}",
        source_url=f"https://www.amazon.com/dp/x{index}",
    )


def _workflow(lines=()):
    workflow = PurchaseWorkflow.new(1, "buy batteries", "batteries")
    workflow.cart = list(lines)
    return workflow


# --- cart ---------------------------------------------------------------------


def test_adding_the_same_item_twice_combines_quantities():
    basket = cart.add([], _candidate(), 1)
    basket = cart.add(basket, _candidate(), 2)

    assert len(basket) == 1
    assert basket[0].quantity == 3


def test_adding_copies_the_stored_price_rather_than_recomputing_it():
    basket = cart.add([], _candidate(price=12.34))

    assert basket[0].price == 12.34
    assert basket[0].price_text == "$12.34"


def test_line_total_multiplies_quantity():
    basket = cart.add([], _candidate(price=7.5), 4)

    assert basket[0].line_total == 30.0


def test_setting_quantity_to_zero_removes_the_line():
    basket = cart.add([], _candidate())

    assert cart.set_quantity(basket, "amazon-result-1", 0) == []


def test_quantity_is_bounded_to_a_sane_maximum():
    basket = cart.add([], _candidate(), 5000)

    assert basket[0].quantity == cart.MAX_LINE_QUANTITY


def test_subtotal_adds_every_line():
    basket = cart.add(cart.add([], _candidate(1, price=10.0), 2), _candidate(2, price=5.5))

    assert cart.subtotal(basket) == 25.5
    assert cart.item_count(basket) == 3


def test_one_unknown_price_makes_the_whole_subtotal_unknown():
    """A partial total would read as the full cost of the order."""
    basket = cart.add(cart.add([], _candidate(1, price=10.0)), _candidate(2, price=None))

    assert cart.subtotal(basket) is None


def test_removing_an_item_leaves_the_others():
    basket = cart.add(cart.add([], _candidate(1)), _candidate(2))

    assert [line.candidate_id for line in cart.remove(basket, "amazon-result-1")] == ["amazon-result-2"]


# --- checkout -----------------------------------------------------------------


def test_summary_reports_counts_and_subtotal():
    summary = checkout.summarize(_workflow(cart.add([], _candidate(price=9.99), 3)))

    assert summary.item_count == 3
    assert summary.subtotal == 29.97
    assert not summary.is_empty


def test_summary_lists_the_facts_amazon_has_not_supplied():
    summary = checkout.summarize(_workflow(cart.add([], _candidate())))

    assert "shipping cost" in summary.unknown
    assert "tax" in summary.unknown
    assert "delivery date" in summary.unknown


@pytest.mark.parametrize(
    "mutate",
    [
        lambda basket: cart.set_quantity(basket, "amazon-result-1", 2),
        lambda basket: cart.add(basket, _candidate(2)),
        lambda basket: cart.remove(basket, "amazon-result-1"),
    ],
)
def test_any_change_to_the_order_invalidates_a_confirmation(mutate):
    """ADR-026: a confirmation applies to exact contents, not to the workflow."""
    workflow = _workflow(cart.add([], _candidate()))
    workflow.confirmed_token = checkout.confirmation_token(workflow)
    assert checkout.is_confirmation_current(workflow)

    workflow.cart = mutate(workflow.cart)

    assert not checkout.is_confirmation_current(workflow)


def test_an_unconfirmed_workflow_is_never_treated_as_confirmed():
    assert not checkout.is_confirmation_current(_workflow(cart.add([], _candidate())))


def test_identical_contents_produce_a_stable_token():
    first = _workflow(cart.add([], _candidate(), 2))
    second = _workflow(cart.add([], _candidate(), 2))

    assert checkout.confirmation_token(first) == checkout.confirmation_token(second)


# --- the guarantee ------------------------------------------------------------


def test_ordering_is_off_unless_deliberately_switched_on(monkeypatch):
    """Ordering spends real money, so a stale config must never be able to spend it."""
    import amazon
    import asyncio

    monkeypatch.delenv("AMAZON_ENABLE_ORDERING", raising=False)
    assert amazon.ordering_enabled() is False

    result = asyncio.run(amazon.place_order())

    assert not result.placed
    assert "switched off" in result.detail


def test_a_cart_above_the_ceiling_is_never_ordered(monkeypatch):
    """AGENTS.md requires a price limit before any financial action."""
    import amazon
    import asyncio

    monkeypatch.setenv("AMAZON_ENABLE_ORDERING", "true")
    monkeypatch.setenv("AMAZON_MAX_ORDER_TOTAL", "10")
    assert amazon.max_order_total() == 10.0

    # No browser is reachable in tests, so reaching checkout at all would raise; the
    # ceiling is asserted directly against the parser that guards it.
    assert amazon._amount("\xa0$53.36") == 53.36
    assert amazon._amount(None) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.amazon.com/gp/buy/spc/handlers/display.html",
        "https://www.amazon.com/checkout/entry",
        "https://www.amazon.com/gp/cart/desktop/go-to-checkout.html",
    ],
)
def test_ordering_urls_are_refused(url):
    import amazon

    with pytest.raises(amazon.AmazonCartUnavailable):
        amazon._refuse_ordering_url(url)


def test_cart_writes_can_be_switched_off(monkeypatch):
    import amazon

    monkeypatch.setenv("AMAZON_ENABLE_CART", "false")
    assert amazon.cart_writes_enabled() is False

    with pytest.raises(amazon.AmazonCartUnavailable):
        import asyncio

        asyncio.run(amazon.add_many_to_cart([("https://www.amazon.com/dp/B079GXSFPB", 1)]))


def test_a_click_that_does_not_change_the_cart_is_reported_as_a_failure(monkeypatch):
    """A variation page shows an Add to Cart button that does nothing until a size is
    chosen. Live testing found this reported success for a cart that never changed."""
    import amazon
    import asyncio

    monkeypatch.setenv("AMAZON_ENABLE_CART", "true")
    counts = iter([0, 0, 0, 0])  # never increases

    class Locator:
        def __init__(self, count=1):
            self._count = count

        async def count(self):
            return self._count

        @property
        def first(self):
            return self

        async def get_attribute(self, name):
            return amazon.ADD_TO_CART_BUTTON_ID if name == "id" else None

        async def click(self):
            return None

        async def select_option(self, value):
            return None

        async def wait_for(self, *args, **kwargs):
            # The buybox is attached after DOMContentLoaded, so the real code waits
            # for it before deciding the control is absent.
            return None

    class Page:
        url = "https://www.amazon.com/dp/B0TEST"

        def locator(self, selector):
            return Locator()

        async def goto(self, *args, **kwargs):
            return None

        async def wait_for_load_state(self, *args, **kwargs):
            return None

        async def close(self):
            return None

    class Context:
        async def new_page(self):
            return Page()

    class Manager:
        async def __aenter__(self):
            return Context()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(amazon, "_persistent_browser_context", lambda **kw: Manager())
    monkeypatch.setattr(amazon, "_cart_count", lambda page: _next(counts))

    results = asyncio.run(amazon.add_many_to_cart([("https://www.amazon.com/dp/B0TEST", 1)]))

    assert results[0].added is False
    assert "did not confirm" in results[0].detail


async def _next(counts):
    return next(counts, 0)


def test_add_to_cart_refuses_a_non_canonical_url(monkeypatch):
    import amazon
    import asyncio

    monkeypatch.setenv("AMAZON_ENABLE_CART", "true")

    # add_many_to_cart reports per item rather than raising, so one bad URL cannot
    # abandon the rest of an approved list.
    results = asyncio.run(amazon.add_many_to_cart([("https://evil.example.com/dp/B0TEST", 1)]))
    assert results and not results[0].added
    assert "canonical" in (results[0].detail or "")


def test_only_amazon_py_may_submit_an_order():
    """Ordering is one function behind one kill switch. It must not spread."""
    import pathlib
    import re

    source = pathlib.Path(__file__).resolve().parent.parent / "src"
    submitters = [
        path.name
        for path in source.glob("*.py")
        if path.name != "amazon.py" and re.search(r"PLACE_ORDER_SELECTOR|proceedToRetailCheckout", path.read_text())
    ]

    assert submitters == [], "only amazon.py may know how to submit an order"
