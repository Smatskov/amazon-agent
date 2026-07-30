import asyncio
from unittest.mock import AsyncMock

import agent
import amazon
import intent_classifier
import workflow_store
from workflow_models import Candidate, PurchaseWorkflow, WorkflowState


def _purchase(product="toothpaste"):
    return intent_classifier.SemanticAction("purchase", "purchase_start", 0.99, product_query=product)


def _products():
    return [
        amazon.Product(
            title="Duracell Coppertop AA Batteries, 24 Count",
            price="$18.49",
            url="https://www.amazon.com/dp/example-aa",
            rating=4.7,
            review_count=1200,
            prime_eligible=True,
        ),
        amazon.Product(
            title="Amazon Basics AA Batteries, 20 Count",
            price="$11.99",
            url="https://www.amazon.com/dp/example-basics",
            rating=4.5,
            review_count=900,
        ),
    ]


def test_validated_purchase_starts_preview_workflow_only(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", AsyncMock(return_value=_purchase()))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    memory_path, workflow_path = tmp_path / "memory.db", tmp_path / "workflows.db"
    response = asyncio.run(agent.agent_brain("Buy toothpaste", memory_path, workflow_path, 41))
    workflow = workflow_store.get_active_workflow(41, workflow_path)
    assert "Amazon results" in response
    assert workflow is not None
    assert workflow.state == WorkflowState.AWAITING_PRODUCT_SELECTION
    assert workflow.normalized_product_goal == "toothpaste"
    assert workflow.candidates[0].title == "Duracell Coppertop AA Batteries, 24 Count"
    assert workflow.candidates[0].source_url == "https://www.amazon.com/dp/example-aa"


def test_observed_sensodyne_request_uses_validated_preview_flow(tmp_path, monkeypatch):
    message = "I was thinking you find the best options for Sensodyne 3 packs. Cheapest"
    monkeypatch.setattr(
        agent.intent_classifier,
        "interpret_message",
        AsyncMock(return_value=_purchase("Sensodyne 3 packs cheapest")),
    )
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    generate = AsyncMock()
    monkeypatch.setattr(agent, "generate_response", generate)

    response = asyncio.run(
        agent.agent_brain(message, tmp_path / "memory.db", tmp_path / "workflows.db", 41)
    )

    assert "Amazon results" in response
    assert "Sensodyne Aa Batteries" not in response
    assert "$18.49" in response
    generate.assert_not_awaited()


def test_purchase_search_failure_does_not_create_fake_or_active_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", AsyncMock(return_value=_purchase("AA batteries")))
    monkeypatch.setattr(
        agent.amazon,
        "search_products",
        AsyncMock(side_effect=amazon.AmazonSearchUnavailable("interstitial")),
    )
    workflow_path = tmp_path / "workflows.db"

    response = asyncio.run(agent.agent_brain("Find AA batteries", tmp_path / "memory.db", workflow_path, 41))

    assert "did not return usable search results" in response
    assert "Sensodyne" not in response
    assert workflow_store.get_active_workflow(41, workflow_path) is None


def test_empty_purchase_search_does_not_create_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", AsyncMock(return_value=_purchase("AA batteries")))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=[]))
    workflow_path = tmp_path / "workflows.db"

    response = asyncio.run(agent.agent_brain("Find AA batteries", tmp_path / "memory.db", workflow_path, 41))

    assert "couldn't find visible Amazon results" in response
    assert workflow_store.get_active_workflow(41, workflow_path) is None


def test_legacy_mock_workflow_is_discarded_before_a_new_real_search(tmp_path, monkeypatch):
    workflow_path = tmp_path / "workflows.db"
    legacy = PurchaseWorkflow.new(41, "Buy AA batteries", "AA batteries")
    legacy.state = WorkflowState.AWAITING_PRODUCT_SELECTION
    legacy.candidates = [
        Candidate("option-1", "Sensodyne AA Batteries", "Sensodyne", 17.99, "Tomorrow", 4.7)
    ]
    workflow_store.save_workflow(legacy, workflow_path)
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", AsyncMock(return_value=_purchase("AA batteries")))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    response = asyncio.run(agent.agent_brain("Find AA batteries", tmp_path / "memory.db", workflow_path, 41))

    assert "Amazon results" in response
    workflow = workflow_store.get_active_workflow(41, workflow_path)
    assert workflow is not None
    assert all(candidate.source_url for candidate in workflow.candidates)


def test_active_workflow_actions_are_validated_before_state_changes(tmp_path, monkeypatch):
    """Only the ambiguous replies reach the model; "the first one" and "cancel" do not."""
    memory_path, workflow_path = tmp_path / "memory.db", tmp_path / "workflows.db"
    interpret = AsyncMock(side_effect=[
        _purchase(),
        intent_classifier.SemanticAction("workflow", "change_quantity", 0.99, quantity=2),
        intent_classifier.SemanticAction("workflow", "confirm", 0.99),
    ])
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", interpret)
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    asyncio.run(agent.agent_brain("Buy toothpaste", memory_path, workflow_path, 3))
    assert "quantity to 2" in asyncio.run(agent.agent_brain("two", memory_path, workflow_path, 3))
    assert "Selected" in asyncio.run(agent.agent_brain("the first one", memory_path, workflow_path, 3))
    assert "not available" in asyncio.run(agent.agent_brain("confirm", memory_path, workflow_path, 3))
    assert "Cancelled" in asyncio.run(agent.agent_brain("cancel", memory_path, workflow_path, 3))

    assert workflow_store.get_active_workflow(3, workflow_path) is None
    assert interpret.await_count == 3


def test_new_purchase_is_not_started_while_a_workflow_is_active(tmp_path, monkeypatch):
    memory_path, workflow_path = tmp_path / "memory.db", tmp_path / "workflows.db"
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", AsyncMock(side_effect=[_purchase("toothpaste"), _purchase("shampoo")]))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    asyncio.run(agent.agent_brain("Buy toothpaste", memory_path, workflow_path, 3))
    response = asyncio.run(agent.agent_brain("Buy shampoo", memory_path, workflow_path, 3))
    assert "already have" in response


def test_every_state_change_advances_the_persisted_state_version(tmp_path, monkeypatch):
    """ADR-026 versioning depends on transitions never bypassing workflow_store."""
    memory_path, workflow_path = tmp_path / "memory.db", tmp_path / "workflows.db"
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", AsyncMock(side_effect=[
        _purchase("AA batteries"),
        intent_classifier.SemanticAction("workflow", "select_candidate", 0.99),
    ]))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    asyncio.run(agent.agent_brain("Buy AA batteries", memory_path, workflow_path, 7))
    after_search = workflow_store.get_active_workflow(7, workflow_path).state_version

    asyncio.run(agent.agent_brain("the first one", memory_path, workflow_path, 7))
    after_selection = workflow_store.get_active_workflow(7, workflow_path).state_version

    assert after_search > 1
    assert after_selection > after_search


def test_stored_workflow_survives_an_unknown_persisted_field(tmp_path):
    """A record written by another version must not make the workflow unreadable."""
    workflow_path = tmp_path / "workflows.db"
    workflow = PurchaseWorkflow.new(9, "Buy AA batteries", "AA batteries")
    workflow.state = WorkflowState.AWAITING_PRODUCT_SELECTION
    workflow.candidates = [
        Candidate("amazon-result-1", "Duracell AA", None, 19.99, source_url="https://www.amazon.com/dp/x")
    ]
    record = workflow.to_record()
    record["retired_workflow_field"] = "ignored"
    record["candidates"][0]["retired_candidate_field"] = "ignored"

    restored = PurchaseWorkflow.from_record(record)

    assert restored.workflow_id == workflow.workflow_id
    assert restored.candidates[0].title == "Duracell AA"


def test_general_chat_preserves_an_active_workflow(tmp_path, monkeypatch):
    memory_path, workflow_path = tmp_path / "memory.db", tmp_path / "workflows.db"
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", AsyncMock(side_effect=[_purchase(), intent_classifier.SemanticAction("general_chat", confidence=0.99)]))
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    monkeypatch.setattr(agent, "generate_response", AsyncMock(return_value="Answer"))
    asyncio.run(agent.agent_brain("Buy toothpaste", memory_path, workflow_path, 3))
    assert asyncio.run(agent.agent_brain("What is France?", memory_path, workflow_path, 3)) == "Answer"
    assert workflow_store.get_active_workflow(3, workflow_path) is not None
