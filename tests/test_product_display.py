"""Presentation shortens and arranges stored facts; it must never add one."""

import pytest

import product_display
import ranking
from ranking import RankedCandidates, SortPreference
from workflow_models import Candidate


def _candidate(title, price=None, price_text=None, rating=None, reviews=None, prime=None):
    return Candidate(
        candidate_id=title[:12],
        title=title,
        brand=None,
        price=price,
        rating=rating,
        price_text=price_text,
        review_count=reviews,
        prime_eligible=prime,
        source_url="https://www.amazon.com/dp/example",
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "Amazon Basics 48 Pack AA Alkaline High-Performance Batteries, 1.5 Volt, Long Lasting Power",
            "Amazon Basics 48 Pack AA Alkaline High-Performance Batteries",
        ),
        (
            "Energizer MAX AA Batteries, 24 Count",
            "Energizer MAX AA Batteries, 24 Count",
        ),
        (
            "Sensodyne Pronamel Gentle Whitening Toothpaste for Sensitive Teeth, "
            "to Reinforce and Protect Enamel, Alpine Breeze - 4 Ounces (Pack of 3)",
            "Sensodyne Pronamel Gentle Whitening Toothpaste for Sensitive Teeth, Pack of 3",
        ),
        ("Short Title", "Short Title"),
    ],
)
def test_display_title_keeps_brand_product_and_pack_size(raw, expected):
    assert product_display.display_title(raw) == expected


def test_display_title_marks_truncation_instead_of_silently_dropping_words():
    raw = "One Two Three Four Five Six Seven Eight Nine Ten Eleven"

    assert product_display.display_title(raw).endswith("…")


def test_display_title_never_invents_a_pack_size():
    assert "Pack" not in product_display.display_title("Generic AA Batteries Long Lasting")


def test_candidate_facts_show_only_supplied_values():
    candidate = _candidate("AA Batteries, 20 Count", price=10.0, price_text="$10.00", rating=4.5, reviews=1234, prime=True)

    facts = product_display.candidate_facts(candidate)

    assert facts == "$10.00 — $0.50 each — 4.5/5 (1,234 reviews) — Prime"


def test_candidate_facts_report_a_missing_price_rather_than_omitting_it():
    facts = product_display.candidate_facts(_candidate("Mystery Item"))

    assert facts == "price not shown"
    assert "each" not in facts


def test_next_step_hint_offers_only_applicable_replies():
    unrated = [_candidate("A", price=1.0), _candidate("B", price=2.0)]

    hint = product_display.next_step_hint(unrated)

    assert "1 to 2" in hint
    assert '"cheapest"' in hint
    assert "highest rated" not in hint


def test_next_step_hint_for_a_single_candidate_does_not_ask_for_a_number():
    hint = product_display.next_step_hint([_candidate("Only one", price=1.0)])

    assert "number" not in hint
    assert '"yes" to choose it' in hint


def test_presented_candidates_are_spaced_and_carry_the_ranking_basis():
    candidates = [
        _candidate("Brand B AA Batteries, 48 Count", price=24.0, price_text="$24.00", rating=4.6, reviews=900),
        _candidate("Brand A AA Batteries, 4 Count", price=6.0, price_text="$6.00", rating=4.1, reviews=20),
    ]
    ranked = ranking.rank(candidates, SortPreference.PRICE)

    message = product_display.present_candidates("AA batteries", ranked)

    assert "ordered by price per item" in message
    assert "\n\n1. Brand B AA Batteries, 48 Count\n$24.00 — $0.50 each" in message
    assert "\n\n2. Brand A AA Batteries, 4 Count\n" in message
    assert product_display.PREVIEW_DISCLAIMER in message


def test_presentation_reports_filtered_results_and_ranking_caveats():
    candidates = [_candidate("Kept Item", price=10.0, price_text="$10.00")]
    ranked = ranking.rank(candidates, SortPreference.PRICE)

    message = product_display.present_candidates(
        "batteries", ranked, removed=2, removal_reasons=("over $20",)
    )

    assert "I left out 2 results (over $20)." in message
    assert "compares total price" in message


def test_empty_result_set_is_stated_plainly_rather_than_shown_as_an_empty_list():
    ranked = RankedCandidates([], ranking.AMAZON_ORDER)

    message = product_display.present_candidates("batteries", ranked)

    assert "could not find any Amazon results" in message
    assert "1." not in message
