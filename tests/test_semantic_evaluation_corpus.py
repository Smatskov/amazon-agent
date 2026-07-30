"""Offline regression corpus for semantic-action contracts, never production routing rules."""

import asyncio
import json

import pytest

import intent_classifier


def _variants(stems, count):
    endings = ["", ".", "!", "?", " please", " now", "", ""]
    return [f"{stem}{endings[index % len(endings)]}" for index, stem in enumerate(stems * ((count // len(stems)) + 1))][:count]


MEMORY_RECALL = _variants([
    "What toothpaste do I like", "What was my toothpaste again", "Remind me about my favorite toothpaste",
    "What did I tell you about toothpaste", "Which toothpaste is my favorite", "What do you have stored for toothpaste",
    "Do you know my favorite toothpaste", "What toothpaste did I say", "Can you remind me what toothpaste I use",
    "Did I ever tell you my favorite toothpaste", "WHAT TOOTHPASTE DO I LIKE", "toothpaste?",
], 100)
MEMORY_REMEMBER = _variants([
    "Remember my favorite toothpaste is Sensodyne", "Save that I use Sensodyne toothpaste",
    "Please keep my toothpaste preference as Sensodyne", "My favorite toothpaste is Sensodyne",
    "Store favorite toothpaste equals Sensodyne", "favorite toothpaste: Sensodyne",
], 100)
MEMORY_FORGET = _variants([
    "Forget my favorite toothpaste", "Remove my toothpaste preference", "Delete the toothpaste I told you",
    "Do not remember that I use Sensodyne", "Clear favorite toothpaste", "erase my toothpaste memory",
], 100)
PURCHASE = _variants([
    "Buy toothpaste", "Get toothpaste", "Order toothpaste", "Need toothpaste", "Running low on toothpaste",
    "Can you grab toothpaste", "Can you purchase toothpaste", "toothpaste please", "TOOTHPASTE", "buy tooth paste",
    "I need more toothpaste", "get me toothpaste", "toothpaste!!!", "Order 2 toothpaste",
], 200)
GENERAL_CHAT = _variants([
    "What is France", "Tell me a joke", "How are you", "Explain batteries", "What is the capital of France",
    "Why is the sky blue", "Write a poem", "Help me understand gravity", "hello", "good morning",
], 100)
MIXED = _variants([
    "Remember I like Sensodyne, then buy toothpaste", "What toothpaste do I like and buy it",
    "Cancel the toothpaste order and tell me a joke", "I need toothpaste but what is France",
    "Forget toothpaste, then get toothpaste", "Buy toothpaste, actually never mind",
], 50)


@pytest.mark.parametrize(
    "corpus, expected_route",
    [(MEMORY_RECALL, "memory"), (MEMORY_REMEMBER, "memory"), (MEMORY_FORGET, "memory"), (PURCHASE, "purchase"), (GENERAL_CHAT, "general_chat"), (MIXED, "unknown")],
)
def test_evaluation_corpus_exercises_the_semantic_contract(monkeypatch, corpus, expected_route):
    """The fixture is an offline stand-in for LM Studio, not a phrase recognizer in production."""
    async def model(prompt, **kwargs):
        if "Route this message" in prompt:
            return json.dumps({"route": expected_route, "confidence": 0.99})
        if expected_route == "memory":
            action = "remember" if corpus is MEMORY_REMEMBER else "forget" if corpus is MEMORY_FORGET else "recall"
            return json.dumps({"action": action, "key": "favorite toothpaste", "value": "Sensodyne" if action == "remember" else None, "confidence": 0.99})
        if expected_route == "purchase":
            return json.dumps({"action": "purchase_start", "product_query": "toothpaste", "constraints": {}, "quantity": None, "confidence": 0.99})
        raise AssertionError("a general or unknown route must not invoke a specialist")

    monkeypatch.setattr(intent_classifier, "generate_response", model)
    results = [asyncio.run(intent_classifier.interpret_message(message)) for message in corpus]
    assert len(results) == len(corpus)
    assert all(result.route == expected_route for result in results)
    if expected_route == "memory":
        assert {result.key for result in results} == {"favorite toothpaste"}
    if expected_route == "purchase":
        assert {result.product_query for result in results} == {"toothpaste"}


def test_corpus_minimums_and_noise_coverage():
    assert len(MEMORY_RECALL) >= 100
    assert len(MEMORY_REMEMBER) >= 100
    assert len(MEMORY_FORGET) >= 100
    assert len(PURCHASE) >= 200
    assert len(GENERAL_CHAT) >= 100
    assert len(MIXED) >= 50
    all_messages = MEMORY_RECALL + MEMORY_REMEMBER + MEMORY_FORGET + PURCHASE + GENERAL_CHAT + MIXED
    assert any(message.isupper() for message in all_messages)
    assert any("!" in message or "?" in message for message in all_messages)
    assert any(len(message.split()) <= 2 for message in all_messages)
