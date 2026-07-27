import asyncio
import json
from unittest.mock import AsyncMock

import pytest

import intent_classifier


@pytest.mark.parametrize(
    "intent, message",
    [
        ("general_chat", "What's the capital of France?"),
        ("memory_remember", "Remember that I prefer Sensodyne."),
        ("memory_recall", "What toothpaste do I usually buy?"),
        ("memory_forget", "Forget my preferred toothpaste."),
        ("amazon_search", "Find AA batteries under twenty dollars."),
        ("amazon_reorder", "Order the toothpaste from last time."),
        ("amazon_buy", "Order AA batteries."),
        ("unknown", "Do the thing with the thing."),
    ],
)
def test_classifier_accepts_each_supported_intent(monkeypatch, intent, message):
    response = json.dumps(
        {
            "intent": intent,
            "confidence": 0.9,
            "entities": {"key": "preferred toothpaste"},
            "reasoning": "test only",
            "requires_confirmation": intent in {"amazon_buy", "amazon_reorder"},
            "extracted_product_name": "AA batteries",
            "extracted_search_query": "AA batteries",
        }
    )
    generate_response = AsyncMock(return_value=response)
    monkeypatch.setattr(intent_classifier, "generate_response", generate_response)

    result = asyncio.run(intent_classifier.classify_intent(message))

    assert result.intent == intent
    assert result.confidence == 0.9
    assert result.entities == {"key": "preferred toothpaste"}
    generate_response.assert_awaited_once()


def test_classifier_falls_back_to_general_chat_for_malformed_json(monkeypatch):
    monkeypatch.setattr(
        intent_classifier, "generate_response", AsyncMock(return_value="not JSON")
    )

    result = asyncio.run(intent_classifier.classify_intent("Find AA batteries."))

    assert result == intent_classifier.IntentResult("general_chat", 0.0, {})


def test_classifier_downgrades_low_confidence_action_to_unknown(monkeypatch):
    response = json.dumps(
        {
            "intent": "amazon_search",
            "confidence": 0.2,
            "entities": {"search_query": "AA batteries"},
            "reasoning": None,
            "requires_confirmation": False,
            "extracted_product_name": None,
            "extracted_search_query": "AA batteries",
        }
    )
    monkeypatch.setattr(
        intent_classifier, "generate_response", AsyncMock(return_value=response)
    )

    result = asyncio.run(intent_classifier.classify_intent("Find AA batteries."))

    assert result.intent == "unknown"
    assert result.confidence == 0.2
