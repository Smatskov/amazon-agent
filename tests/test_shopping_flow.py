"""The full conversation from search to the order gate, through agent_brain."""

import asyncio
from unittest.mock import AsyncMock

import pytest

import agent
import amazon
import intent_classifier
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


def _purchase(query):
    return intent_classifier.SemanticAction("purchase", "purchase_start", 0.99, product_query=query)


def _workflow_action(action, **kwargs):
    return intent_classifier.SemanticAction("workflow", action, 0.99, **kwargs)


def _semantic(monkeypatch, *actions):
    interpret = AsyncMock(side_effect=list(actions))
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", interpret)
    return interpret


def test_picking_an_option_puts_it_on_the_list_with_a_subtotal(paths, monkeypatch):
    _semantic(monkeypatch, _purchase("AA batteries"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("buy AA batteries", paths)
    reply = _run("1", paths)

    assert "Added Duracell Coppertop AA Batteries, 24 Count" in reply
    assert "Subtotal: $18.49" in reply
    assert "nothing has been added to your Amazon cart" in reply
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert workflow.state == WorkflowState.PREPARING_CART
    assert workflow.cart[0].quantity == 1


def test_a_quantity_stated_before_picking_applies_to_that_item_only(paths, monkeypatch):
    _semantic(monkeypatch, _purchase("AA batteries"), _workflow_action("change_quantity", quantity=3))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("buy AA batteries", paths)
    _run("make it three", paths)
    _run("1", paths)
    _run("2", paths)

    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert workflow.cart[0].quantity == 3
    assert workflow.cart[1].quantity == 1


def test_two_searches_build_one_list(paths, monkeypatch):
    _semantic(monkeypatch, _purchase("AA batteries"), _purchase("jockey t shirts"))
    search = AsyncMock(side_effect=[_products(), _shirts()])
    monkeypatch.setattr(agent.amazon, "search_products", search)

    _run("buy AA batteries", paths)
    _run("1", paths)
    _run("now jockey t shirts", paths)
    reply = _run("1", paths)

    assert "Subtotal: $48.48" in reply
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert len(workflow.cart) == 2


def test_candidate_ids_come_from_amazon_identity_not_result_position():
    """Position-based ids collided across searches and merged unrelated cart lines."""
    first = agent._candidates_from_products(_products())
    second = agent._candidates_from_products(_shirts())

    assert first[0].candidate_id == "amazon-a"
    assert second[0].candidate_id == "amazon-t"
    assert {c.candidate_id for c in first}.isdisjoint({c.candidate_id for c in second})


def test_the_same_product_found_twice_keeps_one_identity():
    repeated = agent._candidates_from_products(_products() + _products())

    assert len({candidate.candidate_id for candidate in repeated}) == 2


def test_a_product_without_a_dp_url_still_gets_a_stable_id():
    odd = [amazon.Product("Odd listing", "$1.00", "https://www.amazon.com/gp/other/xyz")]

    first = agent._candidates_from_products(odd)[0].candidate_id
    second = agent._candidates_from_products(odd)[0].candidate_id

    assert first == second
    assert first.startswith("amazon-url-")


def test_removing_by_description_takes_the_right_item_off(paths, monkeypatch):
    _semantic(
        monkeypatch,
        _purchase("AA batteries"),
        _purchase("jockey t shirts"),
        _workflow_action("remove_from_cart"),
    )
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(side_effect=[_products(), _shirts()]))

    _run("buy AA batteries", paths)
    _run("1", paths)
    _run("now jockey t shirts", paths)
    _run("1", paths)
    reply = _run("actually remove the t shirts", paths)

    assert "Removed Jockey" in reply
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert [line.title for line in workflow.cart] == ["Duracell Coppertop AA Batteries, 24 Count"]


def test_add_it_refers_to_the_item_just_picked(paths, monkeypatch):
    _semantic(monkeypatch, _purchase("AA batteries"), _workflow_action("add_to_cart"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("buy AA batteries", paths)
    _run("1", paths)
    reply = _run("ok add it to the cart", paths)

    assert "already on your list (qty 1)" in reply
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert workflow.cart[0].quantity == 1


def test_restating_add_does_not_silently_double_the_quantity(paths, monkeypatch):
    """Quietly buying two of something because the user repeated themselves is costly."""
    _semantic(monkeypatch, _purchase("AA batteries"), _workflow_action("add_to_cart"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("buy AA batteries", paths)
    _run("1", paths)
    _run("add the duracell", paths)

    assert workflow_store.get_active_workflow(USER, paths[1]).cart[0].quantity == 1


def test_a_delivery_question_is_answered_instead_of_ignored(paths, monkeypatch):
    _semantic(monkeypatch, _purchase("Jockey white t shirts medium"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_shirts()))

    reply = _run("How long until Jockey white t shirts medium arrive at my address?", paths)

    assert "can't answer the delivery part yet" in reply
    assert "don't know your address" in reply


def test_ordinary_purchase_requests_carry_no_delivery_note(paths, monkeypatch):
    _semantic(monkeypatch, _purchase("AA batteries"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    reply = _run("buy AA batteries", paths)

    assert "delivery" not in reply


def test_conversation_about_the_list_receives_the_list(paths, monkeypatch):
    """The model must not answer "what's in my cart?" from nothing."""
    _semantic(
        monkeypatch,
        _purchase("AA batteries"),
        intent_classifier.SemanticAction("general_chat", confidence=0.95),
    )
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    generate = AsyncMock(return_value="One item.")
    monkeypatch.setattr(agent, "generate_response", generate)

    _run("buy AA batteries", paths)
    _run("1", paths)
    _run("what's on my list?", paths)

    prompt = generate.await_args.args[0]
    assert "Items on this user's list right now" in prompt
    assert '"quantity": 1' in prompt
    assert "Duracell Coppertop AA Batteries, 24 Count" in prompt


def test_checkout_shows_exact_contents_and_names_what_is_unknown(paths, monkeypatch):
    _semantic(monkeypatch, _purchase("AA batteries"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("buy AA batteries", paths)
    _run("1", paths)
    reply = _run("checkout", paths)

    assert "Order summary" in reply
    assert "Subtotal: $18.49" in reply
    assert "shipping cost" in reply
    assert "The real total will be higher." in reply
    assert workflow_store.get_active_workflow(USER, paths[1]).state == WorkflowState.AWAITING_CHECKOUT_CONFIRMATION


def test_checkout_with_an_empty_list_is_refused(paths, monkeypatch):
    _semantic(monkeypatch, _purchase("AA batteries"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("buy AA batteries", paths)
    reply = _run("checkout", paths)

    assert "nothing to check out" in reply


@pytest.mark.parametrize(
    "message",
    ["place the order", "yes place the order", "confirm", "order it now", "buy it now", "purchase it"],
)
def test_every_way_of_asking_to_buy_reaches_the_refusal(paths, monkeypatch, message):
    """This phrasing must never be answered by a language model."""
    _semantic(monkeypatch, _purchase("AA batteries"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    generate = AsyncMock(return_value="Your order has been placed!")
    monkeypatch.setattr(agent, "generate_response", generate)

    _run("buy AA batteries", paths)
    _run("1", paths)
    _run("checkout", paths)
    reply = _run(message, paths)

    assert "I cannot place this order" in reply
    generate.assert_not_awaited()


def test_confirming_records_approval_but_places_nothing(paths, monkeypatch):
    _semantic(monkeypatch, _purchase("AA batteries"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("buy AA batteries", paths)
    _run("1", paths)
    _run("checkout", paths)
    reply = _run("confirm", paths)

    assert "Confirmed: 1 item(s), items subtotal $18.49" in reply
    assert "cannot place this order" in reply
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert workflow.confirmed_token is not None
    assert workflow.state != WorkflowState.PLACING_ORDER
    assert workflow.state != WorkflowState.COMPLETED


def test_declining_at_the_gate_returns_to_the_list_without_ordering(paths, monkeypatch):
    _semantic(monkeypatch, _purchase("AA batteries"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("buy AA batteries", paths)
    _run("1", paths)
    _run("checkout", paths)
    reply = _run("no", paths)

    assert "Not confirmed — nothing was ordered." in reply
    assert workflow_store.get_active_workflow(USER, paths[1]).state == WorkflowState.PREPARING_CART


def test_changing_the_list_after_confirming_invalidates_the_confirmation(paths, monkeypatch):
    _semantic(monkeypatch, _purchase("AA batteries"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("buy AA batteries", paths)
    _run("1", paths)
    _run("checkout", paths)
    _run("confirm", paths)
    _run("2", paths)

    import checkout as checkout_module

    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert not checkout_module.is_confirmation_current(workflow)


def test_asking_to_confirm_before_checkout_asks_for_the_summary_first(paths, monkeypatch):
    _semantic(monkeypatch, _purchase("AA batteries"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("buy AA batteries", paths)
    _run("1", paths)
    reply = _run("confirm", paths)

    assert "Say 'checkout' first" in reply
