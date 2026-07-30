import asyncio
import json
from unittest.mock import AsyncMock

import pytest

import intent_classifier


def test_router_then_memory_specialist_uses_tiny_json_prompts(monkeypatch):
    generate = AsyncMock(side_effect=[
        json.dumps({"route": "memory", "confidence": 0.99}),
        json.dumps({"action": "recall", "key": "favorite toothpaste", "value": None, "confidence": 0.99}),
    ])
    monkeypatch.setattr(intent_classifier, "generate_response", generate)

    result = asyncio.run(intent_classifier.interpret_message("What was my favorite toothpaste?"))

    assert result.action == "recall"
    assert result.key == "favorite toothpaste"
    assert generate.await_count == 2
    router_prompt = generate.await_args_list[0].args[0]
    specialist_prompt = generate.await_args_list[1].args[0]
    assert "action, key, value" not in router_prompt
    assert "product_query" not in router_prompt
    assert "memory request" in specialist_prompt
    assert generate.await_args_list[0].kwargs["max_tokens"] == 256
    assert generate.await_args_list[1].kwargs["max_tokens"] == 256


@pytest.mark.parametrize("raw", [
    "not json",
    json.dumps({"route": "memory"}),
    json.dumps({"route": "memory", "confidence": 2}),
    json.dumps({"route": "not_a_route", "confidence": 0.9}),
])
def test_invalid_router_output_is_a_safe_no_match(monkeypatch, raw):
    monkeypatch.setattr(intent_classifier, "generate_response", AsyncMock(return_value=raw))
    result = asyncio.run(intent_classifier.interpret_message("Anything"))
    assert result.classification_valid is False
    assert result.action == "no_match"


@pytest.mark.parametrize("raw", [
    json.dumps({"action": "recall", "key": None, "value": None, "confidence": 0.9}),
    json.dumps({"action": "remember", "key": "favorite drink", "value": None, "confidence": 0.9}),
    json.dumps({"action": "recall", "key": "favorite drink", "value": "tea", "confidence": 0.9}),
    json.dumps({"action": "recall", "key": "favorite drink", "value": None, "confidence": 0.2}),
])
def test_memory_specialist_rejects_or_downgrades_unconfident_actions(monkeypatch, raw):
    monkeypatch.setattr(intent_classifier, "generate_response", AsyncMock(side_effect=[
        json.dumps({"route": "memory", "confidence": 0.9}), raw
    ]))
    result = asyncio.run(intent_classifier.interpret_message("Memory request"))
    assert result.action == "no_match"


def test_purchase_specialist_validates_only_explicit_scalar_constraints(monkeypatch):
    generate = AsyncMock(side_effect=[
        json.dumps({"route": "purchase", "confidence": 0.97}),
        json.dumps({"action": "purchase_start", "product_query": "toothpaste", "constraints": {"max_price": 20, "prime": True}, "quantity": 2, "confidence": 0.97}),
    ])
    monkeypatch.setattr(intent_classifier, "generate_response", generate)
    result = asyncio.run(intent_classifier.interpret_message("Buy two toothpaste packs under $20 with Prime"))
    assert result.action == "purchase_start"
    assert result.product_query == "toothpaste"
    assert result.quantity == 2
    assert result.constraints == {"max_price": 20, "prime": True}


def test_general_chat_stops_after_router(monkeypatch):
    generate = AsyncMock(return_value=json.dumps({"route": "general_chat", "confidence": 0.99}))
    monkeypatch.setattr(intent_classifier, "generate_response", generate)
    result = asyncio.run(intent_classifier.interpret_message("What is France?"))
    assert result.route == "general_chat"
    assert generate.await_count == 1
