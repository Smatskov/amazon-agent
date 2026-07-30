import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import agent
import amazon
import intent_classifier
import memory
import product_evaluator
import workflow_store
from response_policy import GENERAL_RESPONSE_MAX_TOKENS, PURCHASING_AGENT_SYSTEM_PROMPT
from workflow_models import WorkflowState


def _action(route, action="no_match", **kwargs):
    return intent_classifier.SemanticAction(route, action, confidence=0.99, **kwargs)


def test_explicit_memory_alias_remains_deterministic(tmp_path: Path, monkeypatch):
    generate = AsyncMock()
    monkeypatch.setattr(agent, "generate_response", generate)
    path = tmp_path / "memory.db"
    assert asyncio.run(agent.agent_brain("remember: favorite toothpaste = Sensodyne", path)) == "Remembered 'favorite toothpaste'."
    assert asyncio.run(agent.agent_brain("recall: favorite toothpaste", path)) == "Memory for 'favorite toothpaste': Sensodyne"
    assert asyncio.run(agent.agent_brain("forget: favorite toothpaste", path)) == "Forgot 'favorite toothpaste'."
    generate.assert_not_awaited()


def test_validated_semantic_memory_action_executes_deterministically(tmp_path, monkeypatch):
    path = tmp_path / "memory.db"
    interpret = AsyncMock(side_effect=[
        _action("memory", "remember", key="favorite toothpaste", value="Sensodyne"),
        _action("memory", "recall", key="favorite toothpaste"),
        _action("memory", "forget", key="favorite toothpaste"),
    ])
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", interpret)
    assert asyncio.run(agent.agent_brain("Remember it naturally", path)) == "Remembered 'favorite toothpaste'."
    assert asyncio.run(agent.agent_brain("Ask naturally", path)) == "Memory for 'favorite toothpaste': Sensodyne"
    assert asyncio.run(agent.agent_brain("Remove it naturally", path)) == "Forgot 'favorite toothpaste'."
    assert memory.recall("favorite toothpaste", path) is None


def test_no_match_continues_to_general_chat_without_usage_text(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", AsyncMock(return_value=_action("unknown")))
    generate = AsyncMock(return_value="Paris")
    monkeypatch.setattr(agent, "generate_response", generate)
    response = asyncio.run(agent.agent_brain("What is the capital of France?", tmp_path / "memory.db"))
    assert response == "Paris"
    assert "usage" not in response.lower()
    assert memory.recall("favorite toothpaste", tmp_path / "memory.db") is None
    generate.assert_awaited_once_with(
        "What is the capital of France?",
        max_tokens=GENERAL_RESPONSE_MAX_TOKENS,
        system_prompt=PURCHASING_AGENT_SYSTEM_PROMPT,
    )


def test_shopping_no_match_asks_what_to_search_and_remembers_asking(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", AsyncMock(return_value=_action("unknown")))
    generate = AsyncMock()
    monkeypatch.setattr(agent, "generate_response", generate)
    workflow_path = tmp_path / "workflows.db"

    response = asyncio.run(
        agent.agent_brain(
            "I was thinking you find the best options for Sensodyne 3 packs. Cheapest",
            tmp_path / "memory.db",
            workflow_path,
            5,
        )
    )

    assert agent.CLARIFICATION_QUESTION in response
    generate.assert_not_awaited()
    pending = workflow_store.get_active_workflow(5, workflow_path)
    assert pending.state == WorkflowState.AWAITING_REQUEST_CLARIFICATION
    assert pending.pending_question == agent.CLARIFICATION_QUESTION


@pytest.mark.parametrize(
    "message, is_shopping",
    [
        ("Find me cheap AA batteries", True),
        ("What is the best price for toothpaste", True),
        ("Explain how research papers are peer reviewed", False),
        ("Tell me about idealism in philosophy", False),
        ("What is the capital of France?", False),
    ],
)
def test_shopping_markers_match_whole_words_only(message, is_shopping):
    """"research" and "idealism" contain "search" and "deal" but are not shopping."""
    assert agent._looks_like_shopping_request(message) is is_shopping


def test_semantic_soft_timeout_is_diagnostic_and_request_continues(tmp_path, monkeypatch, capsys):
    async def delayed_interpret(*args, **kwargs):
        await asyncio.sleep(0.03)
        return _action("memory", "recall", key="favorite toothpaste")

    monkeypatch.setattr(agent.intent_classifier, "interpret_message", delayed_interpret)
    monkeypatch.setattr(agent, "SEMANTIC_SOFT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(agent, "SEMANTIC_HARD_TIMEOUT_SECONDS", 0.08)
    memory.remember("favorite toothpaste", "Sensodyne", tmp_path / "memory.db")

    response = asyncio.run(agent.agent_brain("What toothpaste do I prefer?", tmp_path / "memory.db"))

    assert response == "Memory for 'favorite toothpaste': Sensodyne"
    assert "[TIMING] semantic interpretation exceeded soft timeout (20s)" in capsys.readouterr().out


def test_semantic_hard_timeout_returns_existing_graceful_model_failure(tmp_path, monkeypatch):
    async def never_finishes(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(agent.intent_classifier, "interpret_message", never_finishes)
    monkeypatch.setattr(agent, "SEMANTIC_SOFT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(agent, "SEMANTIC_HARD_TIMEOUT_SECONDS", 0.02)

    response = asyncio.run(agent.agent_brain("Remember something", tmp_path / "memory.db"))

    assert response == agent.LOCAL_MODEL_FAILURE


def test_explicit_search_alias_preserves_existing_read_only_flow(tmp_path, monkeypatch):
    products = [amazon.Product("AA Batteries", "$12.99", "https://example.test")]
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=products))
    monkeypatch.setattr(agent.product_evaluator, "evaluate_products", AsyncMock(return_value=product_evaluator.EvaluationResult("Recommendation", False)))
    assert asyncio.run(agent.agent_brain("search: AA batteries", tmp_path / "memory.db")) == "Recommendation"
