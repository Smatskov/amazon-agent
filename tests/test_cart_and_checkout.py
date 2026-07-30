"""Cart arithmetic, the confirmation gate, and the guarantee that nothing orders."""

import pytest

import cart
import checkout
from checkout import OrderPlacementDisabled
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


def test_place_order_always_refuses():
    with pytest.raises(OrderPlacementDisabled):
        checkout.place_order(_workflow(cart.add([], _candidate())))


def test_no_module_calls_place_order():
    """The refusal is only meaningful if nothing invokes it."""
    import pathlib

    source = pathlib.Path(__file__).resolve().parent.parent / "src"
    callers = [
        path.name
        for path in source.glob("*.py")
        if path.name != "checkout.py" and "place_order" in path.read_text()
    ]

    assert callers == []


def test_amazon_boundary_exposes_no_ordering_capability():
    import amazon

    implemented = {
        name for name in dir(amazon) if not name.startswith("_") and callable(getattr(amazon, name))
    }
    forbidden = {"add_to_cart", "place_order", "place_confirmed_order", "checkout", "buy_now"}

    assert implemented & forbidden == set()


def test_workflow_never_reaches_a_placing_order_state():
    """PLACING_ORDER exists in the enum as a placeholder and must stay unreachable."""
    import pathlib

    agent_source = (pathlib.Path(__file__).resolve().parent.parent / "src" / "agent.py").read_text()

    assert "PLACING_ORDER" not in agent_source
    assert "COMPLETED" not in agent_source
    assert WorkflowState.PLACING_ORDER not in {WorkflowState.PREPARING_CART, WorkflowState.AWAITING_CHECKOUT_CONFIRMATION}
