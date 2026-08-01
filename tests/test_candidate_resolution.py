"""Regression coverage for the candidate references observed to fail in Telegram."""

import pytest

from candidate_resolver import resolve_candidate_reference
from workflow_models import Candidate


def _candidate(index, title, brand=None, price=None, rating=None):
    return Candidate(
        candidate_id=f"amazon-result-{index}",
        title=title,
        brand=brand,
        price=price,
        rating=rating,
        source_url=f"https://www.amazon.com/dp/example-{index}",
    )


@pytest.fixture
def aa_batteries():
    """Mirrors the five real AA results that broke selection in manual testing."""
    return [
        _candidate(1, "Amazon Basics 48 Pack AA Alkaline Batteries", price=17.99, rating=4.6),
        _candidate(2, "Energizer MAX AA Batteries, 24 Count", price=21.49, rating=4.8),
        _candidate(3, "Rayovac AA Batteries, 16 Count Alkaline", price=9.99, rating=4.4),
        _candidate(4, "AmazonBasics AA Performance Alkaline, 8 Pack", price=6.49, rating=4.3),
        _candidate(5, "Duracell Coppertop AA Batteries, 20 Count", price=19.99, rating=4.7),
    ]


@pytest.mark.parametrize("message", ["5", "5.", "option 5", "number 5", "#5", "the fifth one"])
def test_displayed_option_five_is_selectable(aa_batteries, message):
    resolution = resolve_candidate_reference(message, aa_batteries)

    assert resolution.candidate is aa_batteries[4]


@pytest.mark.parametrize(
    "message",
    ["lets do the duracell", "the Duracell one", "Duracell", "I'll take the coppertop"],
)
def test_natural_brand_reference_selects_the_matching_candidate(aa_batteries, message):
    resolution = resolve_candidate_reference(message, aa_batteries)

    assert resolution.candidate is aa_batteries[4]


def test_first_and_last_positions_resolve(aa_batteries):
    assert resolve_candidate_reference("the first one", aa_batteries).candidate is aa_batteries[0]
    assert resolve_candidate_reference("the last one", aa_batteries).candidate is aa_batteries[4]


def test_cheapest_uses_stored_price_not_display_order(aa_batteries):
    resolution = resolve_candidate_reference("the cheapest one", aa_batteries)

    assert resolution.candidate is aa_batteries[3]


def test_highest_rated_uses_stored_rating(aa_batteries):
    resolution = resolve_candidate_reference("highest rated", aa_batteries)

    assert resolution.candidate is aa_batteries[1]


def test_reference_matching_several_candidates_asks_which_one(aa_batteries):
    resolution = resolve_candidate_reference("the alkaline batteries", aa_batteries)

    assert resolution.candidate is None
    assert "More than one option" in resolution.message


def test_pack_count_reference_selects_the_matching_variant(aa_batteries):
    aa_batteries.append(_candidate(6, "Kirkland AA Batteries, 10 Count", price=8.99))

    resolution = resolve_candidate_reference("the 10-count pack", aa_batteries)

    assert resolution.candidate is aa_batteries[5]


def test_pack_count_with_no_matching_variant_asks_instead_of_guessing(aa_batteries):
    resolution = resolve_candidate_reference("the 10-count pack", aa_batteries)

    assert resolution.candidate is None
    # Every candidate matching equally (all of them say "pack") means the words told
    # the agent nothing, so it asks for a number rather than claiming an ambiguous
    # match it cannot actually narrow.
    assert "couldn't tell which option" in resolution.message
    assert not resolution.ambiguous


def test_unmatched_reference_names_the_valid_option_range(aa_batteries):
    resolution = resolve_candidate_reference("the lithium ones", aa_batteries)

    assert resolution.candidate is None
    assert "1 to 5" in resolution.message


def test_out_of_range_number_is_not_silently_clamped(aa_batteries):
    resolution = resolve_candidate_reference("option 9", aa_batteries)

    assert resolution.candidate is None


def test_empty_candidate_list_is_reported_as_absence_not_a_bad_reference():
    resolution = resolve_candidate_reference("the first one", [])

    assert resolution.candidate is None
    assert "no candidates" in resolution.message


def test_comparison_without_the_underlying_fact_refuses_to_guess():
    unpriced = [_candidate(1, "Widget A"), _candidate(2, "Widget B")]

    resolution = resolve_candidate_reference("the cheapest one", unpriced)

    assert resolution.candidate is None
    assert "information needed" in resolution.message
