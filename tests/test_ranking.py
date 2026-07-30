"""Deterministic filtering and ranking must never invent a fact to order by."""

import pytest

import ranking
from ranking import SortPreference
from workflow_models import Candidate


def _candidate(title, price=None, rating=None, reviews=None, prime=None):
    return Candidate(
        candidate_id=title[:12],
        title=title,
        brand=None,
        price=price,
        rating=rating,
        review_count=reviews,
        prime_eligible=prime,
        source_url="https://www.amazon.com/dp/example",
    )


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Amazon Basics 48 Pack AA Alkaline Batteries", 48),
        ("Energizer MAX AA Batteries, 24 Count", 24),
        ("Sensodyne Toothpaste, 4 Ounces (Pack of 3)", 3),
        ("Rayovac AA Batteries 16-Pack", 16),
        ("Duracell AA Batteries, 8 ct", 8),
        ("AA Batteries 1.5 Volt, 10-Year Shelf Life", None),
        ("Plain Product With No Pack Size", None),
    ],
)
def test_pack_count_reads_only_stated_sizes(title, expected):
    assert ranking.pack_count(title) == expected


def test_unit_price_requires_both_price_and_pack_size():
    assert ranking.unit_price(_candidate("AA Batteries, 20 Count", price=10.0)) == 0.5
    assert ranking.unit_price(_candidate("AA Batteries", price=10.0)) is None
    assert ranking.unit_price(_candidate("AA Batteries, 20 Count")) is None


@pytest.mark.parametrize(
    "message, expected",
    [
        ("Find me cheap AA batteries", SortPreference.PRICE),
        ("what is the cheapest option", SortPreference.PRICE),
        ("something affordable please", SortPreference.PRICE),
        ("get the highest rated toothpaste", SortPreference.RATING),
        ("which is best reviewed", SortPreference.RATING),
        ("I need AA batteries", SortPreference.RELEVANCE),
    ],
)
def test_requested_sort_reads_the_users_own_words(message, expected):
    assert ranking.requested_sort(message) is expected


def test_cheap_request_sorts_by_unit_price_not_total_price():
    candidates = [
        _candidate("Brand A AA Batteries, 4 Count", price=6.00),   # $1.50 each
        _candidate("Brand B AA Batteries, 48 Count", price=24.00),  # $0.50 each
        _candidate("Brand C AA Batteries, 10 Count", price=8.00),   # $0.80 each
    ]

    result = ranking.rank(candidates, SortPreference.PRICE)

    assert [c.title[:7] for c in result.candidates] == ["Brand B", "Brand C", "Brand A"]
    assert result.basis == "price per item"
    assert result.caveat is None


def test_unknown_pack_sizes_fall_back_to_total_price_and_say_so():
    candidates = [
        _candidate("Brand A AA Batteries", price=20.00),
        _candidate("Brand B AA Batteries, 48 Count", price=24.00),
    ]

    result = ranking.rank(candidates, SortPreference.PRICE)

    assert [c.title[:7] for c in result.candidates] == ["Brand A", "Brand B"]
    assert result.basis == "total price"
    assert "total price rather than price per item" in result.caveat


def test_results_without_a_price_are_listed_last_and_reported():
    candidates = [
        _candidate("No Price Item, 4 Count"),
        _candidate("Priced Item, 4 Count", price=8.00),
    ]

    result = ranking.rank(candidates, SortPreference.PRICE)

    assert result.candidates[0].title.startswith("Priced")
    assert result.candidates[-1].title.startswith("No Price")
    assert "showed no price" in result.caveat


def test_no_prices_at_all_keeps_amazon_order_instead_of_claiming_a_ranking():
    candidates = [_candidate("First"), _candidate("Second")]

    result = ranking.rank(candidates, SortPreference.PRICE)

    assert [c.title for c in result.candidates] == ["First", "Second"]
    assert result.basis == "Amazon's own result order"
    assert "could not sort by cost" in result.caveat


def test_rating_rank_uses_review_count_to_break_ties():
    candidates = [
        _candidate("Few reviews", rating=4.8, reviews=3),
        _candidate("Many reviews", rating=4.8, reviews=9000),
        _candidate("Lower rating", rating=4.2, reviews=50000),
    ]

    result = ranking.rank(candidates, SortPreference.RATING)

    assert [c.title for c in result.candidates] == ["Many reviews", "Few reviews", "Lower rating"]
    assert result.basis == "customer rating"


def test_relevance_preserves_amazon_order_exactly():
    candidates = [_candidate("B", price=1.0), _candidate("A", price=99.0)]

    result = ranking.rank(candidates, SortPreference.RELEVANCE)

    assert [c.title for c in result.candidates] == ["B", "A"]
    assert result.caveat is None


def test_max_price_constraint_drops_only_proven_violations():
    candidates = [
        _candidate("Cheap", price=10.0),
        _candidate("Expensive", price=40.0),
        _candidate("Unknown price"),
    ]

    outcome = ranking.apply_constraints(candidates, {"max_price": 20})

    assert [c.title for c in outcome.kept] == ["Cheap", "Unknown price"]
    assert outcome.removed == 1
    assert "over $20" in outcome.reasons


def test_prime_constraint_requires_positive_evidence():
    candidates = [
        _candidate("Prime item", prime=True),
        _candidate("Unmarked item"),
    ]

    outcome = ranking.apply_constraints(candidates, {"prime": True})

    assert [c.title for c in outcome.kept] == ["Prime item"]
    assert "not marked Prime" in outcome.reasons


def test_unknown_constraint_keys_are_ignored_rather_than_dropping_everything():
    candidates = [_candidate("Anything", price=10.0)]

    outcome = ranking.apply_constraints(candidates, {"colour": "blue", "latest_refinement": "x"})

    assert outcome.kept == candidates
    assert outcome.removed == 0
