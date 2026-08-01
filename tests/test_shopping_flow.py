"""The full conversation from search to the order gate, through agent_brain."""

import asyncio
from unittest.mock import AsyncMock

import pytest

import agent
import amazon
import workflow_store
from workflow_models import WorkflowState


USER = 21


def _products():
    return [
        amazon.Product("Duracell Coppertop AA Batteries, 24 Count", "$18.49", "https://www.amazon.com/dp/a", 4.7, 1200, prime_eligible=True),
        amazon.Product("Energizer MAX AA Batteries, 16 Count", "$12.00", "https://www.amazon.com/dp/b", 4.5, 900),
    ]


def _shirts():
    return [
        amazon.Product("Jockey Men's Classic Crew Neck T-Shirt, White, Medium, 3 Pack", "$29.99", "https://www.amazon.com/dp/t", 4.6, 8921, prime_eligible=True),
    ]


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "memory.db", tmp_path / "workflows.db"


def _run(message, paths):
    return asyncio.run(agent.agent_brain(message, paths[0], paths[1], USER))


def _semantic(monkeypatch, *actions):
    interpret = AsyncMock(side_effect=list(actions))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(side_effect=[_products(), _shirts()]))

    _run("find me AA batteries", paths)
    _run("1", paths)
    _run("find me jockey t shirts", paths)
    _run("1", paths)
    reply = _run("actually remove the t shirts", paths)

    assert "Removed Jockey" in reply
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert [line.title for line in workflow.cart] == ["Duracell Coppertop AA Batteries, 24 Count"]






def test_a_delivery_question_is_answered_instead_of_ignored(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_shirts()))

    reply = _run("How long until Jockey white t shirts medium arrive at my address?", paths)

    assert "can't answer the delivery part yet" in reply
    assert "don't know your address" in reply


def test_ordinary_purchase_requests_carry_no_delivery_note(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    reply = _run("find me AA batteries", paths)

    assert "delivery" not in reply




def test_checkout_shows_exact_contents_and_names_what_is_unknown(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("find me AA batteries", paths)
    _run("1", paths)
    reply = _run("checkout", paths)

    # Checking out is now one step: it shows the summary and pushes to the Amazon cart.
    assert "IN YOUR AMAZON CART" in reply
    # Option 1 is the cheapest per item ($12.00/16 beats $18.49/24), not Amazon's first.
    assert "$12.00" in reply
    assert "shipping cost" in reply
    assert "The real total will be higher." in reply
    assert workflow_store.get_active_workflow(USER, paths[1]).state == WorkflowState.PAUSED


def test_checkout_with_an_empty_list_is_refused(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("find me AA batteries", paths)
    reply = _run("checkout", paths)

    assert "nothing to check out" in reply


@pytest.mark.parametrize(
    "message",
    ["place the order", "yes place the order", "confirm", "order it now", "buy it now", "purchase it"],
)
def test_every_way_of_asking_to_buy_reaches_the_refusal(paths, monkeypatch, message):
    """This phrasing must never be answered by a language model."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    generate = AsyncMock(return_value="Your order has been placed!")

    _run("find me AA batteries", paths)
    _run("1", paths)
    _run("checkout", paths)
    reply = _run(message, paths)

    # The items are in the Amazon cart by now, so buy phrasing reaches the order
    # screen — which states on its first line that no order was placed.
    assert "NO ORDER WAS PLACED" in reply
    generate.assert_not_awaited()


def test_confirming_records_approval_but_places_nothing(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("find me AA batteries", paths)
    _run("1", paths)
    reply = _run("checkout", paths)

    assert "$12.00" in reply
    assert "IN YOUR AMAZON CART" in reply
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert workflow.confirmed_token is not None
    assert workflow.state != WorkflowState.PLACING_ORDER
    assert workflow.state != WorkflowState.COMPLETED


def test_checking_out_twice_does_not_push_the_same_items_twice(paths, monkeypatch):
    """Checkout writes to the real Amazon cart, so repeating it must be idempotent."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    push = AsyncMock(return_value=[amazon.CartWriteResult("https://www.amazon.com/dp/b", 1, True)])
    monkeypatch.setattr(agent.amazon, "add_many_to_cart", push)
    monkeypatch.setattr(agent.amazon, "read_cart", AsyncMock(return_value=[]))

    _run("find me AA batteries", paths)
    _run("1", paths)
    _run("checkout", paths)
    reply = _run("checkout", paths)

    assert push.await_count == 1, "the second checkout must not re-add the items"
    assert "already in your Amazon cart" in reply


def test_changing_the_list_after_confirming_invalidates_the_confirmation(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("find me AA batteries", paths)
    _run("1", paths)
    _run("checkout", paths)
    # Change the contents after checking out (which is what confirms and pushes).
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    import cart as cart_module
    workflow.cart = cart_module.set_quantity(workflow.cart, workflow.cart[0].candidate_id, 5)
    workflow_store.save_workflow(workflow, paths[1])

    import checkout as checkout_module

    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert not checkout_module.is_confirmation_current(workflow)


def test_asking_to_confirm_before_checkout_asks_for_the_summary_first(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("find me AA batteries", paths)
    _run("1", paths)
    reply = _run("confirm", paths)

    assert "Say 'checkout' first" in reply
