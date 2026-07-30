"""The agent must remember what it asked and act on the answer.

These cover the failures observed in manual Telegram testing: a clarifying question
whose answer was ignored, and short replies that never reached the workflow.
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import agent
import amazon
import intent_classifier
import workflow_store
from workflow_models import WorkflowState


USER = 11


def _products(count=2):
    catalogue = [
        amazon.Product("Duracell Coppertop AA Batteries, 24 Count", "$18.49", "https://www.amazon.com/dp/a", 4.7, 1200, prime_eligible=True),
        amazon.Product("Amazon Basics AA Batteries, 100 Count", "$24.00", "https://www.amazon.com/dp/b", 4.5, 900),
        amazon.Product("Rayovac AA Batteries, 4 Count", "$5.99", "https://www.amazon.com/dp/c", 4.2, 30),
    ]
    return catalogue[:count]


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "memory.db", tmp_path / "workflows.db"


def _run(message, paths):
    return asyncio.run(agent.agent_brain(message, paths[0], paths[1], USER))


def _mock_semantic(monkeypatch, *actions):
    interpret = AsyncMock(side_effect=list(actions))
    monkeypatch.setattr(agent.intent_classifier, "interpret_message", interpret)
    return interpret


def _mock_amazon(monkeypatch, count=2):
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products(count)))


def _age_stored_workflow(database_path, *, hours):
    aged = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with sqlite3.connect(database_path) as connection:
        user_id, payload_json = connection.execute(
            "SELECT telegram_user_id, payload_json FROM purchase_workflows"
        ).fetchone()
        payload = json.loads(payload_json) | {"updated_at": aged}
        connection.execute(
            "UPDATE purchase_workflows SET payload_json = ?, updated_at = ? WHERE telegram_user_id = ?",
            (json.dumps(payload), aged, user_id),
        )


def _unknown():
    return intent_classifier.SemanticAction("unknown", classification_valid=False)


def _purchase(query):
    return intent_classifier.SemanticAction("purchase", "purchase_start", 0.99, product_query=query)


def test_clarifying_question_is_answered_by_the_next_message(paths, monkeypatch):
    _mock_semantic(monkeypatch, _unknown(), _purchase("AA batteries"))
    _mock_amazon(monkeypatch)

    asked = _run("find me the best deal", paths)
    answered = _run("AA batteries", paths)

    assert agent.CLARIFICATION_QUESTION in asked
    assert "Amazon result" in answered
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert workflow.state == WorkflowState.AWAITING_PRODUCT_SELECTION
    assert workflow.normalized_product_goal == "AA batteries"


def test_unrecognised_answer_to_the_question_is_still_used_as_the_search(paths, monkeypatch):
    """The agent asked what to search for, so an unclassified reply is the answer."""
    _mock_semantic(monkeypatch, _unknown(), _unknown())
    search = AsyncMock(return_value=_products())
    monkeypatch.setattr(agent.amazon, "search_products", search)

    _run("find me something", paths)
    _run("Sensodyne 3 pack", paths)

    search.assert_awaited_once_with("Sensodyne 3 pack")


def test_general_chat_during_a_pending_question_does_not_hijack_the_conversation(paths, monkeypatch):
    _mock_semantic(
        monkeypatch,
        _unknown(),
        intent_classifier.SemanticAction("general_chat", confidence=0.99),
    )
    monkeypatch.setattr(agent, "generate_response", AsyncMock(return_value="Paris."))

    _run("find me a good deal", paths)
    reply = _run("what is the capital of France?", paths)

    assert reply == "Paris."
    assert workflow_store.get_active_workflow(USER, paths[1]).state == WorkflowState.AWAITING_REQUEST_CLARIFICATION


@pytest.mark.parametrize("refusal", ["cancel", "no", "never mind"])
def test_refusing_the_clarifying_question_closes_the_workflow(paths, monkeypatch, refusal):
    interpret = _mock_semantic(monkeypatch, _unknown())

    _run("find me a deal", paths)
    reply = _run(refusal, paths)

    assert "Cancelled" in reply
    assert workflow_store.get_active_workflow(USER, paths[1]) is None
    assert interpret.await_count == 1


def test_bare_yes_to_the_clarifying_question_repeats_it_instead_of_searching(paths, monkeypatch):
    _mock_semantic(monkeypatch, _unknown())
    search = AsyncMock()
    monkeypatch.setattr(agent.amazon, "search_products", search)

    _run("find me a deal", paths)
    reply = _run("yes", paths)

    assert reply == agent.CLARIFICATION_QUESTION
    search.assert_not_awaited()


def test_option_number_selects_without_calling_the_model(paths, monkeypatch):
    interpret = _mock_semantic(monkeypatch, _purchase("AA batteries"))
    _mock_amazon(monkeypatch, count=3)

    _run("buy AA batteries", paths)
    reply = _run("3", paths)

    assert "Added Rayovac AA Batteries, 4 Count" in reply
    assert interpret.await_count == 1
    workflow = workflow_store.get_workflow(USER, paths[1])
    assert workflow.selected_candidate_id == workflow.candidates[2].candidate_id


def test_bare_yes_with_several_candidates_asks_which_one(paths, monkeypatch):
    _mock_semantic(monkeypatch, _purchase("AA batteries"))
    _mock_amazon(monkeypatch, count=3)

    _run("find me AA batteries", paths)
    reply = _run("yes", paths)

    assert "still on your search" in reply
    assert "1–3" in reply


def test_bare_yes_with_a_single_candidate_selects_it(paths, monkeypatch):
    _mock_semantic(monkeypatch, _purchase("AA batteries"))
    _mock_amazon(monkeypatch, count=1)

    _run("buy AA batteries", paths)
    reply = _run("yes", paths)

    assert "Added Duracell Coppertop AA Batteries, 24 Count" in reply


def test_unclassified_reply_repeats_the_question_rather_than_answering_something_else(paths, monkeypatch):
    _mock_semantic(monkeypatch, _purchase("AA batteries"), _unknown())
    _mock_amazon(monkeypatch, count=3)
    generate = AsyncMock()
    monkeypatch.setattr(agent, "generate_response", generate)

    _run("buy AA batteries", paths)
    reply = _run("hmm what about something else", paths)

    assert "still on your search" in reply
    generate.assert_not_awaited()


def test_questions_about_the_options_receive_the_option_facts(paths, monkeypatch):
    """Without this the model was asked about products it could not see."""
    _mock_semantic(
        monkeypatch,
        _purchase("AA batteries"),
        intent_classifier.SemanticAction("general_chat", confidence=0.95),
    )
    _mock_amazon(monkeypatch, count=3)
    generate = AsyncMock(return_value="The first is a 24 pack and the second is a 100 pack.")
    monkeypatch.setattr(agent, "generate_response", generate)

    _run("buy AA batteries", paths)
    _run("what's the difference between the first two?", paths)

    prompt = generate.await_args.args[0]
    assert "what's the difference between the first two?" in prompt
    assert "Duracell Coppertop AA Batteries, 24 Count" in prompt
    assert '"unit_price": 0.77' in prompt
    assert '"option": 1' in prompt


def test_general_questions_without_a_workflow_carry_no_product_context(paths, monkeypatch):
    _mock_semantic(monkeypatch, intent_classifier.SemanticAction("general_chat", confidence=0.95))
    generate = AsyncMock(return_value="Paris.")
    monkeypatch.setattr(agent, "generate_response", generate)

    _run("what is the capital of France?", paths)

    assert generate.await_args.args[0] == "what is the capital of France?"


def test_refinement_narrows_the_current_results_instead_of_dead_ending(paths, monkeypatch):
    _mock_semantic(
        monkeypatch,
        _purchase("AA batteries"),
        intent_classifier.SemanticAction("workflow", "refine", 0.95, constraints={"prime": True}),
    )
    _mock_amazon(monkeypatch, count=3)

    _run("buy AA batteries", paths)
    reply = _run("only the Prime ones", paths)

    assert reply.startswith("Narrowed to 1 Amazon result")
    assert "I left out 2 results (not marked Prime)." in reply
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert [c.title for c in workflow.candidates] == ["Duracell Coppertop AA Batteries, 24 Count"]
    assert workflow.state == WorkflowState.AWAITING_PRODUCT_SELECTION


def test_refinement_that_matches_nothing_is_reported_and_not_applied(paths, monkeypatch):
    _mock_semantic(
        monkeypatch,
        _purchase("AA batteries"),
        intent_classifier.SemanticAction("workflow", "refine", 0.95, constraints={"max_price": 1}),
    )
    _mock_amazon(monkeypatch, count=3)

    _run("buy AA batteries", paths)
    reply = _run("nothing over a dollar", paths)

    assert "None of the results I already have meet that" in reply
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert len(workflow.candidates) == 3
    assert "max_price" not in workflow.constraints


def test_refinement_can_reorder_without_a_new_amazon_search(paths, monkeypatch):
    _mock_semantic(
        monkeypatch,
        _purchase("AA batteries"),
        intent_classifier.SemanticAction("workflow", "refine", 0.95),
    )
    search = AsyncMock(return_value=_products(3))
    monkeypatch.setattr(agent.amazon, "search_products", search)

    _run("buy AA batteries", paths)
    reply = _run("show me the cheapest ones", paths)

    assert "ordered by price per item" in reply
    assert search.await_count == 1


def test_an_abandoned_workflow_stops_blocking_new_requests(paths, monkeypatch):
    _mock_semantic(monkeypatch, _purchase("AA batteries"))
    _mock_amazon(monkeypatch, count=2)

    _run("buy AA batteries", paths)
    # save_workflow always refreshes the timestamp, so age the stored payload directly.
    _age_stored_workflow(paths[1], hours=25)

    assert workflow_store.get_active_workflow(USER, paths[1]) is None
    assert workflow_store.get_workflow(USER, paths[1]) is not None


def test_cheap_request_presents_candidates_cheapest_first(paths, monkeypatch):
    _mock_semantic(monkeypatch, _purchase("AA batteries"))
    _mock_amazon(monkeypatch, count=3)

    reply = _run("find me cheap AA batteries", paths)

    assert "ordered by price per item" in reply
    # $24.00/100 = $0.24 each beats $18.49/24 = $0.77 and $5.99/4 = $1.50.
    assert reply.index("Amazon Basics") < reply.index("Duracell")
    assert reply.index("Duracell") < reply.index("Rayovac")
    workflow = workflow_store.get_active_workflow(USER, paths[1])
    assert workflow.candidates[0].title.startswith("Amazon Basics")
