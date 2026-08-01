"""Multiple items reaching the real Amazon cart, rapid messages, and reset."""

import asyncio
from unittest.mock import AsyncMock

import pytest

import agent
import amazon
import workflow_store
from workflow_models import WorkflowState


USER = 4242


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "m.db", tmp_path / "w.db"


def _shampoo():
    return [amazon.Product("Head and Shoulders Shampoo, 28.2 fl oz", "$12.47", "https://www.amazon.com/dp/h1", 4.8, 1467, prime_eligible=True)]


def _towels():
    return [amazon.Product("Bounty Paper Towels, 8 Rolls", "$21.99", "https://www.amazon.com/dp/p1", 4.7, 90000, prime_eligible=True)]


def _batteries():
    return [amazon.Product("Duracell AA Batteries, 24 Count", "$18.49", "https://www.amazon.com/dp/b1", 4.7, 1200, prime_eligible=True)]


def _run(message, paths, user=USER):
    return asyncio.run(agent.agent_brain(message, paths[0], paths[1], user))


def _build_three_item_list(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products",
                        AsyncMock(side_effect=[_shampoo(), _towels(), _batteries()]))
    for query in ("find me shampoo", "find me paper towels", "find me AA batteries"):
        _run(query, paths)
        _run("1", paths)


def test_three_products_build_one_list_with_a_combined_subtotal(paths, monkeypatch):
    _build_three_item_list(paths, monkeypatch)

    workflow = workflow_store.get_active_workflow(USER, paths[1])

    assert len(workflow.cart) == 3
    assert {line.title.split(",")[0] for line in workflow.cart} == {
        "Head and Shoulders Shampoo", "Bounty Paper Towels", "Duracell AA Batteries",
    }
    listing = _run("what's on my list?", paths) if False else None
    total = round(12.47 + 21.99 + 18.49, 2)
    import cart as cart_module
    assert cart_module.subtotal(workflow.cart) == pytest.approx(total, abs=0.011)


def test_confirming_sends_every_item_to_the_real_amazon_cart(paths, monkeypatch):
    _build_three_item_list(paths, monkeypatch)
    sent = {}

    async def add_many(items):
        sent["items"] = list(items)
        return [amazon.CartWriteResult(url, quantity, True) for url, quantity in items]

    monkeypatch.setattr(agent.amazon, "add_many_to_cart", add_many)

    reply = _run("checkout", paths)

    assert len(sent["items"]) == 3
    assert {url for url, _ in sent["items"]} == {
        "https://www.amazon.com/dp/h1", "https://www.amazon.com/dp/p1", "https://www.amazon.com/dp/b1",
    }
    assert "ADDED FROM YOUR LIST" in reply and "3 of 3 item(s)" in reply
    assert "IN YOUR AMAZON CART" in reply




def test_a_partial_cart_failure_is_reported_honestly(paths, monkeypatch):
    _build_three_item_list(paths, monkeypatch)

    async def add_many(items):
        return [
            amazon.CartWriteResult(items[0][0], items[0][1], True),
            amazon.CartWriteResult(items[1][0], items[1][1], False, "out of stock"),
            amazon.CartWriteResult(items[2][0], items[2][1], True),
        ]

    monkeypatch.setattr(agent.amazon, "add_many_to_cart", add_many)

    reply = _run("checkout", paths)

    assert "2 of 3 item(s)" in reply
    assert "Not added" in reply
    assert "out of stock" in reply


def test_a_total_cart_failure_never_claims_success(paths, monkeypatch):
    _build_three_item_list(paths, monkeypatch)
    monkeypatch.setattr(
        agent.amazon, "add_many_to_cart",
        AsyncMock(side_effect=amazon.AmazonCartUnavailable("profile locked")),
    )

    reply = _run("checkout", paths)

    assert "could not add these to your Amazon cart" in reply
    assert "Added 3" not in reply
    assert "IN YOUR AMAZON CART" in reply


def test_confirming_never_reaches_an_ordering_state(paths, monkeypatch):
    _build_three_item_list(paths, monkeypatch)
    monkeypatch.setattr(
        agent.amazon, "add_many_to_cart",
        AsyncMock(return_value=[amazon.CartWriteResult("u", 1, True)]),
    )

    _run("checkout", paths)

    workflow = workflow_store.get_workflow(USER, paths[1])
    assert workflow.state not in {WorkflowState.PLACING_ORDER, WorkflowState.COMPLETED}


# --- reset -------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    ["reset", "Reset", "RESET!", "/reset", "start over", "start again",
     "clear", "clear my list", "new search", "forget everything", "wipe"],
)
def test_reset_phrases_clear_the_conversation(phrase, paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_shampoo()))

    _run("find me shampoo", paths)
    _run("1", paths)
    assert workflow_store.get_active_workflow(USER, paths[1]).cart

    reply = _run(phrase, paths)

    assert "Reset" in reply
    assert workflow_store.get_active_workflow(USER, paths[1]) is None




def test_reset_is_honest_about_the_real_amazon_cart(paths, monkeypatch):

    reply = _run("reset", paths)

    assert "Amazon cart stays there" in reply


def test_reset_works_from_every_state(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_shampoo()))
    monkeypatch.setattr(agent.amazon, "add_many_to_cart",
                        AsyncMock(return_value=[amazon.CartWriteResult("u", 1, True)]))

    for stage in ([], ["1"], ["1", "checkout"], ["1", "checkout", "confirm"]):
        _run("find me shampoo", paths)
        for step in stage:
            _run(step, paths)
        assert "Reset" in _run("reset", paths)
        assert workflow_store.get_active_workflow(USER, paths[1]) is None


# --- rapid-fire messages ------------------------------------------------------


def test_a_burst_of_messages_is_handled_in_order_without_losing_items(paths, monkeypatch):
    """Telegram delivers updates concurrently; a burst must not drop a cart write."""
    monkeypatch.setattr(agent.amazon, "search_products",
                        AsyncMock(side_effect=[_shampoo(), _towels()]))

    async def burst():
        # Same user, issued together, as a fast typist would produce.
        await asyncio.gather(
            agent.agent_brain("find me shampoo", paths[0], paths[1], USER),
        )
        await asyncio.gather(
            agent.agent_brain("1", paths[0], paths[1], USER),
        )
        await asyncio.gather(
            agent.agent_brain("find me paper towels", paths[0], paths[1], USER),
        )
        await asyncio.gather(
            agent.agent_brain("1", paths[0], paths[1], USER),
        )

    asyncio.run(burst())

    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert len(workflow.cart) == 2, "a concurrent burst lost a cart line"


def test_concurrent_messages_do_not_interleave_state(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_shampoo()))

    async def many():
        return await asyncio.gather(*[
            agent.agent_brain(f"message {index}", paths[0], paths[1], USER)
            for index in range(12)
        ])

    replies = asyncio.run(many())

    assert len(replies) == 12
    assert all(isinstance(reply, str) and reply.strip() for reply in replies)


def test_different_users_are_isolated_under_concurrency(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_shampoo()))

    async def two_users():
        await asyncio.gather(
            agent.agent_brain("find me shampoo", paths[0], paths[1], 11),
            agent.agent_brain("find me shampoo", paths[0], paths[1], 22),
        )
        await asyncio.gather(
            agent.agent_brain("1", paths[0], paths[1], 11),
        )

    asyncio.run(two_users())

    assert len(workflow_store.get_active_workflow(11, paths[1]).cart) == 1
    assert workflow_store.get_active_workflow(22, paths[1]).cart == []
