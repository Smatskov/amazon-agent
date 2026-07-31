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


def _presses():
    return [
        amazon.Product("QUQIYSO French Press Coffee Maker 21oz, Copper", "$17.99", "https://www.amazon.com/dp/q1", 4.6, 10994, delivery="Mon, Aug 3"),
        amazon.Product("Bodum 34oz Brazil French Press Coffee Maker, Black", "$19.99", "https://www.amazon.com/dp/b1", 4.5, 19914, delivery="Mon, Aug 3"),
        amazon.Product("Veken French Press Coffee Maker 34oz Stainless", "$24.99", "https://www.amazon.com/dp/v1", 4.4, 5000, delivery="Wed, Aug 5"),
    ]


def _setup(monkeypatch, products, *actions):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=products))


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
    monkeypatch.setattr(agent.amazon, "search_products", search)

    listing = _run("french press", paths)
    assert "1 · " in listing

    _run("2", paths)

    assert search.await_count == 1, "picking option 2 must not search again"
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert len(workflow.cart) == 1
    assert workflow.cart[0].title.startswith("Bodum")


def test_removing_uses_a_menu_rather_than_matching_words(paths, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_presses()))

    _run("french press", paths)
    _run("1", paths)
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    remove_index = next(
        index for index, option in enumerate(workflow.pending_menu, 1)
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
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=[]))

    reply = _run(message, paths, user=8181)

    assert "usage" not in reply.casefold()
    assert "<query>" not in reply


def test_the_colon_aliases_still_work(paths, monkeypatch):

    assert "Remembered" in _run("remember: drink = tea", paths, user=8282)
    assert "tea" in _run("recall: drink", paths, user=8282)


# --- refinement re-searches ---------------------------------------------------


