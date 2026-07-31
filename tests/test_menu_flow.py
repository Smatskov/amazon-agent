"""The menu-driven conversation, including the transcripts that failed in UAT.

The redesign exists because free text meant different things in different places:
"employee" was offered as a way to narrow and then selected instead, and "i prefer the
runner up" started a fresh Amazon search. A number cannot be misread, so every reply
now ends with numbered choices and a numeric answer resolves against exactly those.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import agent
import amazon
import flow
import intent_classifier
import menu
import workflow_store
from menu import MenuAction, MenuOption
from workflow_models import Candidate, PurchaseWorkflow, WorkflowState


USER = 8080


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "m.db", tmp_path / "w.db"


def _run(message, paths, user=USER):
    return asyncio.run(agent.agent_brain(message, paths[0], paths[1], user))


def _purchase(query):
    return intent_classifier.SemanticAction("purchase", "purchase_start", 0.99, product_query=query)


def _presses():
    return [
        amazon.Product("QUQIYSO French Press Coffee Maker 21oz, Copper", "$17.99", "https://www.amazon.com/dp/q1", 4.6, 10994, delivery="Mon, Aug 3"),
        amazon.Product("Bodum 34oz Brazil French Press Coffee Maker, Black", "$19.99", "https://www.amazon.com/dp/b1", 4.5, 19914, delivery="Mon, Aug 3"),
        amazon.Product("Veken French Press Coffee Maker 34oz Stainless", "$24.99", "https://www.amazon.com/dp/v1", 4.4, 5000, delivery="Wed, Aug 5"),
    ]


def _setup(monkeypatch, products, *actions):
    monkeypatch.setattr(
        agent.intent_classifier, "interpret_message",
        AsyncMock(side_effect=list(actions) or [_purchase("french press")]),
    )
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=products))
    monkeypatch.setattr(agent, "generate_response", AsyncMock(return_value="ok"))


# --- menu primitives ----------------------------------------------------------


@pytest.mark.parametrize("reply, index", [("1", 0), ("2", 1), (" 3 ", 2), ("1.", 0), ("#2", 1), ("(3)", 2)])
def test_numeric_replies_resolve(reply, index):
    options = [MenuOption(MenuAction.CANCEL, f"Option {i}") for i in range(1, 4)]

    assert menu.choose(reply, options) is options[index]


@pytest.mark.parametrize("reply", ["", "0", "4", "-1", "one", "1 and 2", "yes", "cancel", "12abc"])
def test_non_choices_return_none(reply):
    options = [MenuOption(MenuAction.CANCEL, f"Option {i}") for i in range(1, 4)]

    assert menu.choose(reply, options) is None


def test_a_number_off_the_end_is_explained_not_guessed():
    options = [MenuOption(MenuAction.CANCEL, "Only option")]

    assert "no option 7" in menu.out_of_range_hint("7", options)
    assert menu.out_of_range_hint("1", options) is None


def test_a_menu_survives_persistence(tmp_path):
    workflow = PurchaseWorkflow.new(1, "x", "y")
    workflow.pending_menu = [MenuOption(MenuAction.SELECT, "Add thing", "amazon-1")]
    workflow_store.save_workflow(workflow, tmp_path / "w.db")

    restored = workflow_store.get_workflow(1, tmp_path / "w.db")

    assert restored.pending_menu == workflow.pending_menu
    assert menu.choose("1", restored.pending_menu).payload == "amazon-1"


# --- the UAT transcripts ------------------------------------------------------


def test_the_runner_up_can_be_chosen_without_a_new_search(paths, monkeypatch):
    """UAT: "i prefer the runner up" searched Amazon for marathon t-shirts."""
    search = AsyncMock(return_value=_presses())
    monkeypatch.setattr(agent.intent_classifier, "interpret_message",
                        AsyncMock(return_value=_purchase("french press")))
    monkeypatch.setattr(agent.amazon, "search_products", search)

    recommended = _run("add the best available french press to my cart", paths)
    assert "runner-up" in recommended

    chosen = _run("2", paths)

    assert search.await_count == 1, "picking the runner-up must not search again"
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert len(workflow.cart) == 1
    assert workflow.cart[0].title.startswith("Bodum")


def test_narrowing_is_offered_as_a_choice_not_as_free_text(paths, monkeypatch):
    """UAT: the hint said to type a brand to narrow, but typing it selected instead."""
    _setup(monkeypatch, _presses(), _purchase("french press"))

    listing = _run("find me a french press", paths)

    assert "Narrow these results" in listing
    narrowing = _run("4", paths)

    assert "narrow by" in narrowing.casefold()
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert workflow.cart == [], "narrowing must never add anything"


def test_the_list_is_never_mistaken_for_search_results(paths, monkeypatch):
    """UAT: the user read their own list as new suggestions."""
    _setup(monkeypatch, _presses(), _purchase("french press"))

    results = _run("find me a french press", paths)
    listing = _run("1", paths)

    assert results.startswith("🔎") and "Results for" in results
    assert "🧺" in listing and "Your list" in listing
    assert "Results for" not in listing


# --- the flow -----------------------------------------------------------------


def test_every_reply_offers_numbered_choices(paths, monkeypatch):
    _setup(monkeypatch, _presses(), _purchase("french press"))

    listing = _run("find me a french press", paths)
    assert "1 · " in listing

    added = _run("1", paths)
    assert "1 · " in added

    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert workflow.pending_menu


def test_a_menu_choice_never_calls_the_model(paths, monkeypatch):
    interpret = AsyncMock(return_value=_purchase("french press"))
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", interpret)
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_presses()))

    _run("find me a french press", paths)
    _run("1", paths)
    _run("1", paths)

    assert interpret.await_count == 1


def test_the_whole_purchase_runs_on_numbers_alone(paths, monkeypatch):
    monkeypatch.setattr(agent.intent_classifier, "interpret_message",
                        AsyncMock(return_value=_purchase("french press")))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_presses()))
    pushed = {}

    async def add_many(items):
        pushed["items"] = list(items)
        return [amazon.CartWriteResult(u, q, True) for u, q in items]

    monkeypatch.setattr(agent.amazon, "add_many_to_cart", add_many)

    _run("find me a french press", paths)          # results
    _run("1", paths)                                # add first
    listing = _run("1", paths)                      # check out
    assert "Order summary" in listing
    done = _run("1", paths)                         # confirm

    assert "cannot place this order" in done
    assert len(pushed["items"]) == 1
    workflow = workflow_store.get_workflow(USER, paths[1])
    assert workflow.state not in {WorkflowState.PLACING_ORDER, WorkflowState.COMPLETED}


def test_confirming_twice_cannot_push_to_amazon_twice(paths, monkeypatch):
    """The checkout menu used to stay pending, so "1" confirmed again."""
    monkeypatch.setattr(agent.intent_classifier, "interpret_message",
                        AsyncMock(return_value=_purchase("french press")))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_presses()))
    calls = []

    async def add_many(items):
        calls.append(list(items))
        return [amazon.CartWriteResult(u, q, True) for u, q in items]

    monkeypatch.setattr(agent.amazon, "add_many_to_cart", add_many)

    _run("find me a french press", paths)
    _run("1", paths)
    _run("1", paths)   # checkout
    _run("1", paths)   # confirm
    _run("1", paths)   # whatever option 1 is now, it must not be "confirm" again

    assert len(calls) == 1


def test_an_out_of_range_number_is_explained(paths, monkeypatch):
    _setup(monkeypatch, _presses(), _purchase("french press"))

    _run("find me a french press", paths)
    reply = _run("99", paths)

    assert "no option 99" in reply
    assert workflow_store.get_active_workflow(USER, paths[1]).cart == []


def test_removing_uses_a_menu_rather_than_matching_words(paths, monkeypatch):
    _setup(monkeypatch, _presses(), _purchase("french press"))

    _run("find me a french press", paths)
    _run("1", paths)
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    remove_index = next(
        i for i, option in enumerate(workflow.pending_menu, 1)
        if option.action is MenuAction.REMOVE
    )

    prompt = _run(str(remove_index), paths)
    assert "remove" in prompt.casefold()

    _run("1", paths)
    assert workflow_store.get_active_workflow(USER, paths[1]).cart == []


# --- bare command words no longer hijack English ------------------------------


@pytest.mark.parametrize("message", ["search", "remember", "recall", "forget"])
def test_a_bare_command_word_is_not_developer_usage_text(message, paths, monkeypatch):
    """UAT: typing "search" answered with "Search usage: search: <query>."."""
    monkeypatch.setattr(
        agent.intent_classifier, "interpret_message",
        AsyncMock(return_value=intent_classifier.SemanticAction("unknown", classification_valid=False)),
    )
    monkeypatch.setattr(agent, "generate_response", AsyncMock(return_value="What should I look for?"))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=[]))

    reply = _run(message, paths, user=8181)

    assert "usage" not in reply.casefold()
    assert "<query>" not in reply


def test_the_colon_aliases_still_work(paths, monkeypatch):
    monkeypatch.setattr(agent, "generate_response", AsyncMock())

    assert "Remembered" in _run("remember: drink = tea", paths, user=8282)
    assert "tea" in _run("recall: drink", paths, user=8282)


# --- refinement re-searches ---------------------------------------------------


def test_a_budget_refinement_searches_again_for_a_full_set(paths, monkeypatch):
    """UAT: "under $16" left two irrelevant leftovers instead of finding options."""
    cheap = [
        amazon.Product(f"Cheap Press {i}", f"$1{i}.99", f"https://www.amazon.com/dp/c{i}", 4.5, 100)
        for i in range(1, 5)
    ]
    search = AsyncMock(side_effect=[_presses(), cheap])
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", AsyncMock(side_effect=[
        _purchase("french press"),
        intent_classifier.SemanticAction("workflow", "refine", 0.95, constraints={"max_price": 16}),
    ]))
    monkeypatch.setattr(agent.amazon, "search_products", search)

    _run("find me a french press", paths)
    reply = _run("under $16", paths)

    assert search.await_count == 2, "a budget should fetch fresh options"
    assert reply.count("· <b>") >= 3
