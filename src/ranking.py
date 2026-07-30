"""Deterministic hard filtering and inspectable ranking of stored candidates.

Ordering and budget checks are arithmetic and policy, so they belong in code rather
than in a model prompt. Nothing here contacts Amazon, the model, or storage, and
nothing here invents a fact: a candidate that does not carry the value being compared
is ranked last and reported as missing, never guessed.
"""

from dataclasses import dataclass
from enum import StrEnum
import re

from workflow_models import Candidate


# "Pack of 12", "24 Count", "48-Pack", "8 ct". Product nouns are deliberately excluded
# so "1.5 Volt" and "10-Year Shelf Life" cannot be read as a pack size.
PACK_COUNT = re.compile(
    r"\b(?:pack|count|set|box)\s+of\s+(\d+)\b"
    r"|\b(\d+)\s*[-– ]?\s*(?:packs?|counts?|ct|cts|pk|pcs|pieces?)\b"
)
MAX_REASONABLE_PACK = 1000
CHEAP_REQUEST = re.compile(r"\b(?:cheap|budget|affordable|inexpensive|lowest[- ]price|least expensive)")
RATING_REQUEST = re.compile(r"\b(?:highest|best|top)[- ]?(?:rated|reviewed|rating)\b|\bmost reviews\b")


AMAZON_ORDER = "Amazon's own result order"
PREVIOUS_ORDER = "their previous order"
# Bases that describe a position rather than a measure, for phrasing.
POSITIONAL_BASES = frozenset({AMAZON_ORDER, PREVIOUS_ORDER})


class SortPreference(StrEnum):
    PRICE = "price"
    RATING = "rating"
    RELEVANCE = "relevance"


@dataclass(frozen=True, slots=True)
class FilterOutcome:
    """Candidates that satisfy every hard constraint, plus what was dropped and why."""

    kept: list[Candidate]
    removed: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RankedCandidates:
    """An ordering plus the evidence that produced it."""

    candidates: list[Candidate]
    basis: str
    caveat: str | None = None


def pack_count(title: str) -> int | None:
    """Read a pack size stated in the title, or None when it is not stated."""
    match = PACK_COUNT.search(title.casefold())
    if not match:
        return None
    value = int(match.group(1) or match.group(2))
    return value if 0 < value <= MAX_REASONABLE_PACK else None


def unit_price(candidate: Candidate) -> float | None:
    """Price per item, only when both the price and the pack size are known."""
    count = pack_count(candidate.title)
    if candidate.price is None or not count:
        return None
    return candidate.price / count


def requested_sort(message: str) -> SortPreference:
    """Read an explicit ordering preference from the user's own words."""
    lower = message.casefold()
    if RATING_REQUEST.search(lower):
        return SortPreference.RATING
    if CHEAP_REQUEST.search(lower):
        return SortPreference.PRICE
    return SortPreference.RELEVANCE


def apply_constraints(
    candidates: list[Candidate], constraints: dict | None
) -> FilterOutcome:
    """Drop candidates that violate a stated hard constraint; ignore unknown keys."""
    if not constraints:
        return FilterOutcome(list(candidates), 0)

    max_price = _number(constraints.get("max_price"))
    min_rating = _number(constraints.get("min_rating"))
    prime_required = constraints.get("prime") is True or constraints.get("prime_required") is True

    kept: list[Candidate] = []
    reasons: list[str] = []
    for candidate in candidates:
        # A missing fact is not a violation; it cannot prove the constraint is broken.
        if max_price is not None and candidate.price is not None and candidate.price > max_price:
            _add(reasons, f"over ${max_price:g}")
            continue
        if min_rating is not None and candidate.rating is not None and candidate.rating < min_rating:
            _add(reasons, f"rated under {min_rating:g}")
            continue
        if prime_required and candidate.prime_eligible is not True:
            _add(reasons, "not marked Prime")
            continue
        kept.append(candidate)
    return FilterOutcome(kept, len(candidates) - len(kept), tuple(reasons))


def rank(candidates: list[Candidate], preference: SortPreference) -> RankedCandidates:
    """Order candidates by the requested basis, reporting the basis and its limits."""
    if not candidates or preference is SortPreference.RELEVANCE:
        return RankedCandidates(list(candidates), AMAZON_ORDER)
    if preference is SortPreference.RATING:
        return _rank_by_rating(candidates)
    return _rank_by_price(candidates)


def _rank_by_price(candidates: list[Candidate]) -> RankedCandidates:
    priced = [candidate for candidate in candidates if candidate.price is not None]
    unpriced = [candidate for candidate in candidates if candidate.price is None]
    if not priced:
        return RankedCandidates(list(candidates), AMAZON_ORDER, "None of these showed a price, so I could not sort by cost.")

    if all(unit_price(candidate) is not None for candidate in priced):
        ordered = sorted(priced, key=unit_price)
        basis = "price per item"
        caveat = None
    else:
        ordered = sorted(priced, key=lambda candidate: candidate.price)
        basis = "total price"
        caveat = "Pack sizes are not stated for every result, so this compares total price rather than price per item."

    if unpriced:
        caveat = _join(caveat, f"{len(unpriced)} result(s) showed no price and are listed last.")
    return RankedCandidates(ordered + unpriced, basis, caveat)


def _rank_by_rating(candidates: list[Candidate]) -> RankedCandidates:
    rated = [candidate for candidate in candidates if candidate.rating is not None]
    unrated = [candidate for candidate in candidates if candidate.rating is None]
    if not rated:
        return RankedCandidates(list(candidates), AMAZON_ORDER, "None of these showed a rating, so I could not sort by rating.")
    # Review count breaks ties so a lone 5-star review does not outrank a strong average.
    ordered = sorted(rated, key=lambda candidate: (-candidate.rating, -(candidate.review_count or 0)))
    caveat = f"{len(unrated)} result(s) showed no rating and are listed last." if unrated else None
    return RankedCandidates(ordered + unrated, "customer rating", caveat)


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _add(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _join(existing: str | None, addition: str) -> str:
    return f"{existing} {addition}" if existing else addition
