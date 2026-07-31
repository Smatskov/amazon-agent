"""Command vs question, delivery-aware ranking, and the curated example corpus."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest

import agent
import amazon
import examples
import intent_classifier
import ranking
import request_mode
import workflow_store
from ranking import SortPreference
from request_mode import RequestMode
from workflow_models import Candidate


# --- command vs question ------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "add shampoo to my cart",
        "buy me some paper towels",
        "order AA batteries",
        "reorder my usual coffee",
        "get me the cheapest AA batteries you can find",
        "please add it to my cart",
    ],
)
def test_instructions_are_commands(message):
    assert request_mode.classify(message) is RequestMode.COMMAND


@pytest.mark.parametrize(
    "message",
    [
        "find me shampoo",
        "what shampoo is good",
        "show me AA batteries",
        "looking for paper towels",
        "which of these is organic",
        "AA batteries",
    ],
)
def test_look_ups_are_browse(message):
    assert request_mode.classify(message) is RequestMode.BROWSE


# --- delivery ----------------------------------------------------------------


def test_delivery_days_reads_a_stated_date():
    assert ranking.delivery_days("Tue, Aug 4", today=date(2026, 7, 30)) == 5


def test_delivery_days_rolls_a_past_month_into_next_year():
    """Amazon omits the year, so January in December means next January."""
    assert ranking.delivery_days("Fri, Jan 8", today=date(2026, 12, 30)) == 9


def test_delivery_days_is_none_without_a_date():
    assert ranking.delivery_days(None) is None
    assert ranking.delivery_days("arrives soon") is None


def test_fast_request_sorts_by_delivery_date():
    assert ranking.requested_sort("I need these fastest") is SortPreference.DELIVERY

    candidates = [
        Candidate("a", "Slow item", None, 5.0, "Fri, Dec 25"),
        Candidate("b", "Quick item", None, 9.0, "Mon, Dec 1"),
    ]
    result = ranking.rank(candidates, SortPreference.DELIVERY)

    assert [c.candidate_id for c in result.candidates] == ["b", "a"]
    assert result.basis == "delivery date"


def test_undated_results_are_listed_last_and_reported():
    candidates = [
        Candidate("a", "No date", None, 5.0, None),
        Candidate("b", "Dated", None, 9.0, "Mon, Dec 1"),
    ]

    result = ranking.rank(candidates, SortPreference.DELIVERY)

    assert result.candidates[0].candidate_id == "b"
    assert "no delivery date" in result.caveat


# --- recommendation ----------------------------------------------------------


def test_accuracy_outranks_price_so_the_cheapest_wrong_item_does_not_win():
    candidates = [
        Candidate("hs", "Head and Shoulders Classic Clean Dandruff Shampoo", None, 14.97, "Tue, Aug 4", 4.7),
        Candidate("gen", "Generic Clarifying Shampoo", None, 3.96, "Fri, Aug 14", 4.0),
    ]

    pick = ranking.recommend(candidates, "i need head and shoulders dandruff shampoo")

    assert pick.candidate.candidate_id == "hs"
    assert "matches everything you asked for" in pick.reasons
    assert pick.runner_up.candidate_id == "gen"


def test_recommendation_states_why_using_only_supplied_facts():
    candidates = [
        Candidate("a", "Brand A AA Batteries", None, 5.0, "Mon, Aug 3", 4.8),
        Candidate("b", "Brand B AA Batteries", None, 25.0, "Fri, Aug 20", 4.1),
    ]

    pick = ranking.recommend(candidates, "AA batteries")

    assert pick.candidate.candidate_id == "a"
    assert any("soonest" in reason for reason in pick.reasons)
    assert any("lowest price" in reason for reason in pick.reasons)


def test_recommendation_of_nothing_is_none():
    assert ranking.recommend([], "anything") is None


# --- curated examples --------------------------------------------------------


def test_corpus_loads_and_covers_the_known_failure_cases():
    records = examples.load()
    messages = {record["message"] for record in records}

    assert len(records) >= 30
    for known in ("cheapest", "place the order", "i need head and shoulders", "3"):
        assert known in messages


def test_similar_examples_are_word_matched():
    matches = examples.similar("place the order now")

    assert matches
    assert any("order" in record["message"] for record in matches)


def test_prompt_block_is_empty_when_nothing_matches():
    assert examples.prompt_block("zzzz qqqq") == ""


def test_a_malformed_line_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        '{"message": "good", "route": "memory", "action": "recall"}\n'
        "{not json at all\n"
        '{"message": "also good", "route": "workflow", "action": "cancel"}\n'
    )
    examples.reset_cache()

    records = examples.load(path)

    assert [record["message"] for record in records] == ["good", "also good"]
    examples.reset_cache()


def test_a_missing_corpus_does_not_break_routing(tmp_path):
    examples.reset_cache()

    assert examples.load(tmp_path / "absent.jsonl") == ()
    examples.reset_cache()


# --- command mode end to end -------------------------------------------------


def _products():
    return [
        amazon.Product("Head and Shoulders Classic Clean Dandruff Shampoo", "$14.97", "https://www.amazon.com/dp/h1", 4.7, 210000, delivery="Tue, Aug 4"),
        amazon.Product("Generic Clarifying Shampoo", "$3.96", "https://www.amazon.com/dp/g1", 4.0, 500, delivery="Fri, Aug 14"),
    ]


def test_a_command_recommends_one_and_waits_for_yes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent.intent_classifier,
        "interpret_message",
        AsyncMock(return_value=intent_classifier.SemanticAction(
            "purchase", "purchase_start", 0.99, product_query="head and shoulders shampoo")),
    )
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    paths = (tmp_path / "m.db", tmp_path / "w.db")

    recommended = asyncio.run(agent.agent_brain("add head and shoulders shampoo to my cart", *paths, 31))

    assert "Best match" in recommended
    assert "Head and Shoulders" in recommended
    assert "lowest price" in recommended or "matches" in recommended
    # Nothing is on the list until the user agrees.
    assert workflow_store.get_active_workflow(31, paths[1]).cart == []

    accepted = asyncio.run(agent.agent_brain("yes", *paths, 31))

    assert "Added Head and Shoulders" in accepted
    assert len(workflow_store.get_active_workflow(31, paths[1]).cart) == 1


def test_options_shows_the_full_list_after_a_recommendation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent.intent_classifier,
        "interpret_message",
        AsyncMock(return_value=intent_classifier.SemanticAction(
            "purchase", "purchase_start", 0.99, product_query="shampoo")),
    )
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))
    paths = (tmp_path / "m.db", tmp_path / "w.db")

    asyncio.run(agent.agent_brain("add shampoo to my cart", *paths, 32))
    listing = asyncio.run(agent.agent_brain("options", *paths, 32))

    assert "1 ·" in listing and "2 ·" in listing
    assert "1 ·" in listing


def test_a_browse_request_still_shows_every_option(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent.intent_classifier,
        "interpret_message",
        AsyncMock(return_value=intent_classifier.SemanticAction(
            "purchase", "purchase_start", 0.99, product_query="shampoo")),
    )
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    reply = asyncio.run(agent.agent_brain("find me shampoo", tmp_path / "m.db", tmp_path / "w.db", 33))

    assert "Results for" in reply
    assert "Best match" not in reply


def test_delivery_dates_reach_the_user(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent.intent_classifier,
        "interpret_message",
        AsyncMock(return_value=intent_classifier.SemanticAction(
            "purchase", "purchase_start", 0.99, product_query="shampoo")),
    )
    monkeypatch.setattr(agent.amazon, "search_products", AsyncMock(return_value=_products()))

    reply = asyncio.run(agent.agent_brain("find me shampoo", tmp_path / "m.db", tmp_path / "w.db", 34))

    assert "arrives Tue, Aug 4" in reply
