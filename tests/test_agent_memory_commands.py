import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import agent
import amazon
import intent_classifier
import memory
import product_evaluator
from request_context import RequestContext


def test_remember_command_stores_value_without_calling_llm(tmp_path: Path, monkeypatch):
    generate_response = AsyncMock()
    monkeypatch.setattr(agent, "generate_response", generate_response)
    database_path = tmp_path / "memory.db"

    response = asyncio.run(
        agent.agent_brain(" ReMeMbEr: favorite_toothpaste = Sensodyne ", database_path)
    )

    assert response == "Remembered 'favorite_toothpaste'."
    assert memory.recall("favorite_toothpaste", database_path) == "Sensodyne"
    generate_response.assert_not_awaited()


def test_recall_command_returns_stored_value_without_calling_llm(
    tmp_path: Path, monkeypatch
):
    generate_response = AsyncMock()
    monkeypatch.setattr(agent, "generate_response", generate_response)
    database_path = tmp_path / "memory.db"
    memory.remember("favorite_toothpaste", "Sensodyne", database_path)

    response = asyncio.run(agent.agent_brain("RECALL: favorite_toothpaste", database_path))

    assert response == "Memory for 'favorite_toothpaste': Sensodyne"
    generate_response.assert_not_awaited()


def test_missing_recall_returns_deterministic_response_without_calling_llm(
    tmp_path: Path, monkeypatch
):
    generate_response = AsyncMock()
    monkeypatch.setattr(agent, "generate_response", generate_response)

    response = asyncio.run(agent.agent_brain("recall: missing", tmp_path / "memory.db"))

    assert response == "Nothing is stored for 'missing'."
    generate_response.assert_not_awaited()


def test_forget_command_removes_value_without_calling_llm(tmp_path: Path, monkeypatch):
    generate_response = AsyncMock()
    monkeypatch.setattr(agent, "generate_response", generate_response)
    database_path = tmp_path / "memory.db"
    memory.remember("favorite_toothpaste", "Sensodyne", database_path)

    response = asyncio.run(agent.agent_brain("forget: favorite_toothpaste", database_path))

    assert response == "Forgot 'favorite_toothpaste'."
    assert memory.recall("favorite_toothpaste", database_path) is None
    generate_response.assert_not_awaited()


def test_malformed_remember_command_returns_usage(tmp_path: Path, monkeypatch):
    generate_response = AsyncMock()
    monkeypatch.setattr(agent, "generate_response", generate_response)

    response = asyncio.run(agent.agent_brain("remember favorite_toothpaste", tmp_path / "memory.db"))

    assert response == agent.MEMORY_USAGE
    generate_response.assert_not_awaited()


def test_empty_memory_key_returns_usage(tmp_path: Path, monkeypatch):
    generate_response = AsyncMock()
    monkeypatch.setattr(agent, "generate_response", generate_response)

    response = asyncio.run(agent.agent_brain("remember: = Sensodyne", tmp_path / "memory.db"))

    assert response == agent.MEMORY_USAGE
    generate_response.assert_not_awaited()


def test_ordinary_message_still_calls_llm(tmp_path: Path, monkeypatch):
    generate_response = AsyncMock(return_value="Local model reply")
    classify_intent = AsyncMock(
        return_value=intent_classifier.IntentResult("general_chat", 0.9, {})
    )
    monkeypatch.setattr(agent, "generate_response", generate_response)
    monkeypatch.setattr(agent.intent_classifier, "classify_intent", classify_intent)

    response = asyncio.run(agent.agent_brain("What toothpaste should I buy?", tmp_path / "memory.db"))

    assert response == "Local model reply"
    generate_response.assert_awaited_once_with("What toothpaste should I buy?")


def test_natural_memory_remember_routes_to_existing_memory_storage(
    tmp_path: Path, monkeypatch
):
    classify_intent = AsyncMock(
        return_value=intent_classifier.IntentResult(
            "memory_remember",
            0.95,
            {"key": "preferred toothpaste", "value": "Sensodyne"},
        )
    )
    generate_response = AsyncMock()
    monkeypatch.setattr(agent.intent_classifier, "classify_intent", classify_intent)
    monkeypatch.setattr(agent, "generate_response", generate_response)
    database_path = tmp_path / "memory.db"

    response = asyncio.run(
        agent.agent_brain("Remember that I prefer Sensodyne.", database_path)
    )

    assert response == "Remembered 'preferred toothpaste'."
    assert memory.recall("preferred toothpaste", database_path) == "Sensodyne"
    generate_response.assert_not_awaited()


def test_search_command_calls_amazon_and_passes_structured_results_to_evaluator(
    tmp_path: Path, monkeypatch
):
    products = [
        amazon.Product(
            title="Reliable AA Batteries",
            price="$12.99",
            url="https://www.amazon.com/example",
            rating=4.6,
            review_count=1200,
        )
    ]
    search_products = AsyncMock(
        return_value=products
    )
    evaluate_products = AsyncMock(
        return_value=product_evaluator.EvaluationResult(
            recommendation="The batteries look like a strong option.",
            appears_to_be_reorder=False,
        )
    )
    monkeypatch.setattr(agent.amazon, "search_products", search_products)
    monkeypatch.setattr(agent.product_evaluator, "evaluate_products", evaluate_products)

    response = asyncio.run(agent.agent_brain("search: AA batteries", tmp_path / "memory.db"))

    assert response == "The batteries look like a strong option."
    search_products.assert_awaited_once_with("AA batteries")
    context, evaluated_products = evaluate_products.await_args.args
    assert context == RequestContext(
        original_user_request="search: AA batteries",
        intent="amazon_search",
        search_query="AA batteries",
        confidence=1.0,
    )
    assert evaluated_products == products


def test_natural_search_routes_to_existing_amazon_and_evaluator(
    tmp_path: Path, monkeypatch
):
    products = [amazon.Product("AA Batteries", "$12.99", "https://example.test")]
    classify_intent = AsyncMock(
        return_value=intent_classifier.IntentResult(
            "amazon_search",
            0.92,
            {"search_query": "AA batteries under twenty dollars"},
            extracted_search_query="AA batteries under twenty dollars",
        )
    )
    search_products = AsyncMock(return_value=products)
    evaluate_products = AsyncMock(
        return_value=product_evaluator.EvaluationResult("Recommendation", False)
    )
    monkeypatch.setattr(agent.intent_classifier, "classify_intent", classify_intent)
    monkeypatch.setattr(agent.amazon, "search_products", search_products)
    monkeypatch.setattr(agent.product_evaluator, "evaluate_products", evaluate_products)

    response = asyncio.run(
        agent.agent_brain("Find AA batteries under twenty dollars.", tmp_path / "memory.db")
    )

    assert response == "Recommendation"
    search_products.assert_awaited_once_with("AA batteries under twenty dollars")
    context, evaluated_products = evaluate_products.await_args.args
    assert context.intent == "amazon_search"
    assert context.confidence == 0.92
    assert evaluated_products == products


def test_search_command_requires_a_query_without_calling_external_boundaries(
    tmp_path: Path, monkeypatch
):
    search_products = AsyncMock()
    generate_response = AsyncMock()
    monkeypatch.setattr(agent.amazon, "search_products", search_products)
    monkeypatch.setattr(agent, "generate_response", generate_response)

    response = asyncio.run(agent.agent_brain("search:   ", tmp_path / "memory.db"))

    assert response == agent.SEARCH_USAGE
    search_products.assert_not_awaited()
    generate_response.assert_not_awaited()
