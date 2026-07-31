"""Search creates a workflow from real Amazon records, and nothing else does."""

import asyncio
from unittest.mock import AsyncMock

import agent
import amazon
import workflow_store
from workflow_models import Candidate, PurchaseWorkflow, WorkflowState


def _products():
    return [
        amazon.Product("Duracell Coppertop AA Batteries, 24 Count", "$18.49",
                       "https://www.amazon.com/dp/example-aa", 4.7, 1200, prime_eligible=True),
        amazon.Product("Amazon Basics AA Batteries, 20 Count", "$11.99",
                       "https://www.amazon.com/dp/example-basics", 4.5, 900),
    ]


def _run(message, tmp_path, user=41):
    return asyncio.run(
        agent.agent_brain(message, tmp_path / "memory.db", tmp_path / "workflows.db", user)
    )


def test_a_search_creates_a_workflow_from_real_records(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    response = _run("AA batteries", tmp_path)

    workflow = workflow_store.get_active_workflow(41, tmp_path / "workflows.db")
    assert "Results for" in response
    assert workflow.state == WorkflowState.AWAITING_PRODUCT_SELECTION
    assert workflow.candidates[0].title == "Duracell Coppertop AA Batteries, 24 Count"
    assert workflow.candidates[0].source_url == "https://www.amazon.com/dp/example-aa"
    assert workflow.pending_menu, "results must always offer numbered choices"


def test_the_raw_message_is_what_gets_searched(tmp_path, monkeypatch):
    """Amazon understands ordinary phrasing better than a rewritten query."""
    search = AsyncMock(return_value=_products())
    monkeypatch.setattr(agent.amazon, "search_products", search)

    _run("alright, i need a new iphone 17 charger", tmp_path)

    assert search.await_args.args[0] == "iphone 17 charger"


def test_amazon_failure_creates_no_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent.amazon, "search_products",
        AsyncMock(side_effect=amazon.AmazonSearchUnavailable("interstitial")),
    )

    response = _run("AA batteries", tmp_path)

    assert "usable search results" in response
    assert workflow_store.get_active_workflow(41, tmp_path / "workflows.db") is None


def test_an_empty_search_creates_no_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=[]))

    response = _run("AA batteries", tmp_path)

    assert "couldn't find" in response.casefold()
    assert workflow_store.get_active_workflow(41, tmp_path / "workflows.db") is None


def test_legacy_fabricated_candidates_are_discarded(tmp_path, monkeypatch):
    workflow_path = tmp_path / "workflows.db"
    legacy = PurchaseWorkflow.new(41, "Buy AA batteries", "AA batteries")
    legacy.state = WorkflowState.AWAITING_PRODUCT_SELECTION
    legacy.candidates = [Candidate("option-1", "Sensodyne AA Batteries", "Sensodyne", 17.99, "Tomorrow", 4.7)]
    workflow_store.save_workflow(legacy, workflow_path)
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    response = _run("AA batteries", tmp_path)

    assert "Results for" in response
    workflow = workflow_store.get_active_workflow(41, workflow_path)
    assert all(candidate.source_url for candidate in workflow.candidates)


def test_a_second_search_keeps_the_list(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    _run("AA batteries", tmp_path, user=3)
    _run("1", tmp_path, user=3)
    response = _run("shampoo", tmp_path, user=3)

    assert "Results for" in response
    workflow = workflow_store.get_active_workflow(3, tmp_path / "workflows.db")
    assert workflow.normalized_product_goal == "shampoo"
    assert len(workflow.cart) == 1, "the earlier pick must survive a new search"


def test_every_state_change_advances_the_persisted_version(tmp_path, monkeypatch):
    """ADR-026 versioning depends on transitions never bypassing workflow_store."""
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    workflow_path = tmp_path / "workflows.db"

    _run("AA batteries", tmp_path, user=7)
    after_search = workflow_store.get_active_workflow(7, workflow_path).state_version

    _run("1", tmp_path, user=7)
    after_pick = workflow_store.get_active_workflow(7, workflow_path).state_version

    assert after_search > 1
    assert after_pick > after_search


def test_a_stored_workflow_survives_an_unknown_field(tmp_path):
    """A record written by another version must not make the workflow unreadable."""
    workflow = PurchaseWorkflow.new(9, "Buy AA batteries", "AA batteries")
    workflow.candidates = [
        Candidate("amazon-x", "Duracell AA", None, 19.99, source_url="https://www.amazon.com/dp/x")
    ]
    record = workflow.to_record()
    record["retired_workflow_field"] = "ignored"
    record["candidates"][0]["retired_candidate_field"] = "ignored"

    restored = PurchaseWorkflow.from_record(record)

    assert restored.workflow_id == workflow.workflow_id
    assert restored.candidates[0].title == "Duracell AA"
