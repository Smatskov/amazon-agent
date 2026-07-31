"""The invariant that makes invented products impossible.

A prompt told the model "Never invent product facts". It invented "Garden Bug Spray,
16oz, organic formula" anyway. So the control is no longer a prompt: the model cannot
write anything the user sees, because no shopping path calls it and no reply is built
from its output.
"""

import asyncio
import pathlib
from unittest.mock import AsyncMock

import pytest

import agent
import amazon
import state_answer
import workflow_store


SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

SHOPPING_MESSAGES = [
    "bug spray", "iphone 17 charger", "alright, i need a new iphone 17 charger",
    "order coffee filters", "add the best french press to my cart", "1", "2",
    "checkout", "confirm", "cancel", "the larger size", "under $20",
    "is there anything in my cart?", "how much is it?", "what did you show me?",
    "which of these is organic?", "what is the capital of France?", "reset",
]


def _products():
    return [
        amazon.Product("OFF! Deep Woods Insect Repellent, 6 oz", "$5.92",
                       "https://www.amazon.com/dp/o1", 4.6, 30000, delivery="Mon, Aug 3"),
        amazon.Product("Cutter Backwoods Insect Repellent, 6 oz", "$4.88",
                       "https://www.amazon.com/dp/c1", 4.7, 15000, delivery="Tue, Aug 4"),
    ]


@pytest.mark.parametrize("message", SHOPPING_MESSAGES)
def test_no_shopping_message_ever_reaches_the_model(message, tmp_path, monkeypatch):
    """conftest makes any real model call raise; this proves none is attempted."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    called = []

    async def watch(*args, **kwargs):
        called.append(args)
        raise AssertionError("the shopping path must never consult the model")

    monkeypatch.setattr(agent.intent_classifier, "interpret_memory", watch)

    reply = asyncio.run(agent.agent_brain(message, tmp_path / "m.db", tmp_path / "w.db", 1))

    assert called == [], f"{message!r} consulted the model"
    assert reply.strip()


def test_a_whole_purchase_runs_without_the_model(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    monkeypatch.setattr(
        agent.amazon, "add_many_to_cart",
        AsyncMock(return_value=[amazon.CartWriteResult("u", 1, True)]),
    )

    async def watch(*args, **kwargs):
        raise AssertionError("the purchase path must never consult the model")

    monkeypatch.setattr(agent.intent_classifier, "interpret_memory", watch)
    paths = (tmp_path / "m.db", tmp_path / "w.db")

    def run(message):
        return asyncio.run(agent.agent_brain(message, *paths, 2))

    run("bug spray")
    run("1")          # add
    run("1")          # checkout
    done = run("1")   # confirm

    assert "cannot place this order" in done
    assert len(workflow_store.get_workflow(2, paths[1]).cart) == 1


def test_no_module_builds_a_reply_from_model_output():
    """Only intent_classifier may call the model, and only for memory."""
    offenders = []
    for path in SRC.glob("*.py"):
        if path.name in {"intent_classifier.py", "llm_client.py"}:
            continue
        source = path.read_text()
        if "generate_response" in source:
            offenders.append(path.name)
    assert offenders == [], f"these modules can still emit model text: {offenders}"


def test_the_chat_prompt_is_gone():
    """The prompt that failed to prevent invented products no longer exists."""
    assert not (SRC / "response_policy.py").exists()
    assert not (SRC / "product_evaluator.py").exists()
    for path in SRC.glob("*.py"):
        assert "PURCHASING_AGENT_SYSTEM_PROMPT" not in path.read_text()


@pytest.mark.parametrize(
    "message, expected",
    [
        ("is there anything in my cart?", "empty"),
        ("what's on my list?", "empty"),
        ("how much is it?", "nothing to total"),
        ("what did you show me?", "haven't shown you any results"),
    ],
)
def test_state_questions_are_answered_from_stored_state(message, expected):
    """These used to reach the model, which answered "I don't have access"."""
    answer = state_answer.answer(message, None)

    assert answer is not None
    assert expected in answer


def test_an_attribute_question_answers_from_titles_only():
    from workflow_models import Candidate, PurchaseWorkflow

    workflow = PurchaseWorkflow.new(1, "shampoo", "shampoo")
    workflow.candidates = [
        Candidate("a", "Avalon Organics Biotin Shampoo", None, 10.0),
        Candidate("b", "Head and Shoulders Classic", None, 8.0),
    ]

    answer = state_answer.answer("which of these is organic?", workflow)

    assert "Option 1" in answer
    assert "can't verify" in answer


def test_a_non_question_falls_through_to_searching():
    assert state_answer.answer("bug spray", None) is None
    assert state_answer.answer("iphone 17 charger", None) is None
