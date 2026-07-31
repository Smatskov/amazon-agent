"""Memory is the only place a language model is still consulted."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import agent
import intent_classifier
import memory
from intent_classifier import MemoryRequest


def test_explicit_memory_alias_remains_deterministic(tmp_path: Path, monkeypatch):
    interpret = AsyncMock()
    monkeypatch.setattr(agent.intent_classifier, "interpret_memory", interpret)
    path = tmp_path / "memory.db"

    assert asyncio.run(agent.agent_brain("remember: favorite toothpaste = Sensodyne", path)) == "Remembered 'favorite toothpaste'."
    assert asyncio.run(agent.agent_brain("recall: favorite toothpaste", path)) == "Memory for 'favorite toothpaste': Sensodyne"
    assert asyncio.run(agent.agent_brain("forget: favorite toothpaste", path)) == "Forgot 'favorite toothpaste'."
    interpret.assert_not_awaited()


def test_natural_memory_phrasing_reaches_the_model(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent.intent_classifier, "interpret_memory",
        AsyncMock(return_value=MemoryRequest("remember", "favorite shampoo", "Dove", 0.9)),
    )
    path = tmp_path / "memory.db"

    reply = asyncio.run(agent.agent_brain("remember my favorite shampoo is Dove", path))

    assert reply == "Remembered 'favorite shampoo'."
    assert memory.recall("favorite shampoo", path) == "Dove"


@pytest.mark.parametrize(
    "message",
    ["bug spray", "find me AA batteries", "1", "checkout", "iphone 17 charger"],
)
def test_shopping_never_consults_the_model(message, tmp_path, monkeypatch):
    """The model is gated behind memory phrasing; shopping must never wait on it."""
    interpret = AsyncMock()
    monkeypatch.setattr(agent.intent_classifier, "interpret_memory", interpret)
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=[]))

    asyncio.run(agent.agent_brain(message, tmp_path / "m.db", tmp_path / "w.db", 5))

    interpret.assert_not_awaited()


def test_an_unavailable_model_falls_through_to_searching(tmp_path, monkeypatch):
    """A memory-shaped message still works as a search when the model is down."""
    monkeypatch.setattr(
        agent.intent_classifier, "interpret_memory",
        AsyncMock(return_value=intent_classifier.NO_MATCH),
    )
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(agent.amazon, "search_products", search)

    reply = asyncio.run(
        agent.agent_brain("remember to get me shampoo", tmp_path / "m.db", tmp_path / "w.db", 6)
    )

    search.assert_awaited()
    assert reply.strip()


# --- validation ---------------------------------------------------------------


def test_a_valid_memory_payload_is_accepted():
    request = intent_classifier.validate(
        {"action": "recall", "key": "favorite drink", "value": None, "confidence": 0.9}
    )

    assert request.action == "recall"
    assert request.key == "favorite drink"
    assert request.is_actionable


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"action": "recall"},
        {"action": "recall", "key": None, "value": None, "confidence": 0.9},
        {"action": "remember", "key": "drink", "value": None, "confidence": 0.9},
        {"action": "recall", "key": "drink", "value": "tea", "confidence": 0.9},
        {"action": "recall", "key": "drink", "value": None, "confidence": 0.2},
        {"action": "not_an_action", "key": "drink", "value": None, "confidence": 0.9},
        {"action": "recall", "key": "drink", "value": None, "confidence": 2},
        {"action": "recall", "key": "drink", "value": None, "confidence": "high"},
        {"action": "no_match", "key": None, "value": None, "confidence": 0.9},
        {"action": "recall", "key": "drink", "value": None, "confidence": 0.9, "extra": 1},
    ],
)
def test_anything_unexpected_fails_closed(payload):
    assert intent_classifier.validate(payload).is_actionable is False


def test_a_model_error_is_a_no_match(monkeypatch):
    async def explode(*args, **kwargs):
        raise ConnectionError("LM Studio is down")

    monkeypatch.setattr(intent_classifier, "generate_response", explode)

    assert asyncio.run(intent_classifier.interpret_memory("remember x")).is_actionable is False


def test_malformed_json_is_a_no_match(monkeypatch):
    monkeypatch.setattr(
        intent_classifier, "generate_response", AsyncMock(return_value="not json {{{")
    )

    assert asyncio.run(intent_classifier.interpret_memory("remember x")).is_actionable is False
