import asyncio
from unittest.mock import AsyncMock

import amazon
import product_evaluator
from request_context import RequestContext


def test_request_context_records_current_search_facts_only():
    context = RequestContext(
        original_user_request="search: AA batteries",
        intent="product_search",
        search_query="AA batteries",
        confidence=1.0,
    )

    assert context.requires_confirmation is False
    assert context.future_order_history_candidate is None


def test_evaluator_uses_structured_products_without_searching_amazon(monkeypatch):
    products = [
        amazon.Product(
            title="Reliable AA Batteries",
            price="$12.99",
            url="https://www.amazon.com/example",
            rating=4.6,
            review_count=1200,
            availability="In Stock",
            prime_eligible=True,
        )
    ]
    generate_response = AsyncMock(return_value="Top choice: Reliable AA Batteries.")
    search_products = AsyncMock()
    monkeypatch.setattr(product_evaluator, "generate_response", generate_response)
    monkeypatch.setattr(amazon, "search_products", search_products)

    context = RequestContext(
        original_user_request="search: AA batteries",
        intent="product_search",
        search_query="AA batteries",
        confidence=1.0,
    )

    result = asyncio.run(product_evaluator.evaluate_products(context, products))

    assert result.recommendation == "Top choice: Reliable AA Batteries."
    assert result.appears_to_be_reorder is False
    search_products.assert_not_awaited()
    prompt = generate_response.await_args.args[0]
    assert "Use only the structured product data" in prompt
    assert "Prime eligibility only when it is provided" in prompt
    assert '"title": "Reliable AA Batteries"' in prompt
    assert '"prime_eligible": true' in prompt


def test_evaluator_flags_reorder_style_request_without_history_access(monkeypatch):
    generate_response = AsyncMock(return_value="I can compare the current results.")
    search_products = AsyncMock()
    monkeypatch.setattr(product_evaluator, "generate_response", generate_response)
    monkeypatch.setattr(amazon, "search_products", search_products)
    context = RequestContext(
        original_user_request="search: reorder coffee",
        intent="product_search",
        search_query="reorder coffee",
        confidence=1.0,
    )

    result = asyncio.run(product_evaluator.evaluate_products(context, []))

    assert result.appears_to_be_reorder is True
    search_products.assert_not_awaited()
    assert "Reorder-request signal: True" in generate_response.await_args.args[0]
