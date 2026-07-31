"""Deterministic hard filtering and inspectable ranking of stored candidates.

Ordering and budget checks are arithmetic and policy, so they belong in code rather
than in a model prompt. Nothing here contacts Amazon, the model, or storage, and
nothing here invents a fact: a candidate that does not carry the value being compared
is ranked last and reported as missing, never guessed.
"""

from dataclasses import dataclass
from datetime import date
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


MONTHS = {
    name: number
    for number, name in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1
    )
}
DELIVERY_PARTS = re.compile(
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})", re.IGNORECASE
)
FAST_REQUEST = re.compile(r"\b(?:fastest|soonest|quickest|asap|today|tomorrow|overnight|fast delivery)\b")


class SortPreference(StrEnum):
    PRICE = "price"
    RATING = "rating"
    DELIVERY = "delivery"
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


def delivery_days(label: str | None, *, today: date | None = None) -> int | None:
    """Days until a stated delivery date, or None when Amazon stated none.

    The year is absent from Amazon's label, so a month already past is read as next
    year rather than as a date hundreds of days in the past.
    """
    if not label:
        return None
    match = DELIVERY_PARTS.search(label)
    if not match:
        return None
    today = today or date.today()
    month = MONTHS[match.group(1).casefold()]
    day = int(match.group(2))
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate >= today:
            return (candidate - today).days
    return None


def requested_sort(message: str) -> SortPreference:
    """Read an explicit ordering preference from the user's own words."""
    lower = message.casefold()
    if FAST_REQUEST.search(lower):
        return SortPreference.DELIVERY
    if RATING_REQUEST.search(lower):
        return SortPreference.RATING
    if CHEAP_REQUEST.search(lower):
        return SortPreference.PRICE
    return SortPreference.RELEVANCE


MAX_PRICE_PHRASE = re.compile(
    r"(?:under|below|less than|cheaper than|max|no more than|<)\s*\$?\s*(\d+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
MIN_RATING_PHRASE = re.compile(
    r"(?:rated|rating|stars?)\D{0,12}(\d(?:\.\d)?)|(\d(?:\.\d)?)\s*stars?\s*(?:or\s*(?:more|up|better|above))?",
    re.IGNORECASE,
)
PRIME_PHRASE = re.compile(r"\bprime\b", re.IGNORECASE)
CONSTRAINT_NOISE = frozenset(
    """only just the a an and or with under below less than more over cheaper max show me
    ones one option options please product products that is are be by for""".split()
)


def parse_constraint(message: str) -> dict:
    """Read a narrowing instruction deterministically.

    "under $20", "only prime", "4 stars or more", or a bare brand word. Anything left
    over becomes a keyword the title must contain, which is how a brand narrows.
    """
    constraints: dict = {}
    lowered = (message or "").casefold()

    price = MAX_PRICE_PHRASE.search(lowered)
    if price:
        constraints["max_price"] = float(price.group(1))
    if PRIME_PHRASE.search(lowered):
        constraints["prime"] = True
    rating = MIN_RATING_PHRASE.search(lowered)
    if rating and "star" in lowered:
        value = rating.group(1) or rating.group(2)
        if value and 0 < float(value) <= 5:
            constraints["min_rating"] = float(value)

    consumed = MAX_PRICE_PHRASE.sub(" ", lowered)
    consumed = PRIME_PHRASE.sub(" ", consumed)
    words = [
        word
        for word in re.findall(r"[a-z0-9'&-]{3,}", consumed)
        if word not in CONSTRAINT_NOISE and not word.isdigit()
    ]
    if words:
        constraints["keyword"] = " ".join(words)
    return constraints


def apply_constraints(
    candidates: list[Candidate], constraints: dict | None
) -> FilterOutcome:
    """Drop candidates that violate a stated hard constraint; ignore unknown keys."""
    if not constraints:
        return FilterOutcome(list(candidates), 0)

    max_price = _number(constraints.get("max_price"))
    min_rating = _number(constraints.get("min_rating"))
    prime_required = constraints.get("prime") is True or constraints.get("prime_required") is True
    keyword = constraints.get("keyword")
    keyword_words = (
        [word for word in str(keyword).casefold().split() if word] if keyword else []
    )

    kept: list[Candidate] = []
    reasons: list[str] = []
    for candidate in candidates:
        if keyword_words:
            title = candidate.title.casefold()
            if not any(word in title for word in keyword_words):
                _add(reasons, f"not matching '{keyword}'")
                continue
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
    if preference is SortPreference.DELIVERY:
        return _rank_by_delivery(candidates)
    return _rank_by_price(candidates)


def _rank_by_delivery(candidates: list[Candidate]) -> RankedCandidates:
    dated = [c for c in candidates if delivery_days(c.delivery_label) is not None]
    undated = [c for c in candidates if delivery_days(c.delivery_label) is None]
    if not dated:
        return RankedCandidates(
            list(candidates),
            AMAZON_ORDER,
            "None of these showed a delivery date, so I could not sort by speed.",
        )
    ordered = sorted(dated, key=lambda c: delivery_days(c.delivery_label))
    caveat = (
        f"{len(undated)} result(s) showed no delivery date and are listed last."
        if undated
        else None
    )
    return RankedCandidates(ordered + undated, "delivery date", caveat)


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


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One pick, with the evidence that chose it stated in plain words."""

    candidate: Candidate
    reasons: tuple[str, ...]
    runner_up: Candidate | None = None


def _accuracy(candidate: Candidate, request_terms: set[str]) -> float:
    """Share of the user's own words that appear in the title."""
    if not request_terms:
        return 0.0
    title_words = {_fold(word) for word in re.findall(r"[a-z0-9]+", candidate.title.casefold())}
    matched = sum(1 for term in request_terms if _fold(term) in title_words)
    return matched / len(request_terms)


def _fold(word: str) -> str:
    if word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def recommend(candidates: list[Candidate], request: str) -> Recommendation | None:
    """Pick the single best candidate on request accuracy, price, and delivery speed.

    Weighted deliberately: matching what the user asked for outranks being cheap,
    because the cheapest wrong product is not a good answer. Every component is a fact
    Amazon supplied; a missing fact scores neutral rather than being invented.
    """
    if not candidates:
        return None

    terms = {
        word
        for word in re.findall(r"[a-z0-9]+", request.casefold())
        if len(word) > 2 and word not in _REQUEST_NOISE
    }
    unit_prices = [p for p in (unit_price(c) or c.price for c in candidates) if p is not None]
    cheapest, dearest = (min(unit_prices), max(unit_prices)) if unit_prices else (None, None)
    day_values = [d for d in (delivery_days(c.delivery_label) for c in candidates) if d is not None]
    soonest, latest = (min(day_values), max(day_values)) if day_values else (None, None)

    scored: list[tuple[float, Candidate, tuple[str, ...]]] = []
    for candidate in candidates:
        reasons: list[str] = []
        accuracy = _accuracy(candidate, terms)
        if accuracy >= 0.99 and terms:
            reasons.append("matches everything you asked for")

        effective = unit_price(candidate) or candidate.price
        price_score = 0.5
        if effective is not None and cheapest is not None and dearest is not None and dearest > cheapest:
            price_score = 1 - (effective - cheapest) / (dearest - cheapest)
            if effective == cheapest:
                reasons.append("lowest price per item" if unit_price(candidate) else "lowest price")

        days = delivery_days(candidate.delivery_label)
        speed_score = 0.5
        if days is not None and soonest is not None and latest is not None and latest > soonest:
            speed_score = 1 - (days - soonest) / (latest - soonest)
            if days == soonest:
                reasons.append(f"arrives soonest ({candidate.delivery_label})")
        elif days is not None:
            reasons.append(f"arrives {candidate.delivery_label}")

        rating_score = (candidate.rating or 0) / 5
        total = 0.45 * accuracy + 0.25 * price_score + 0.20 * speed_score + 0.10 * rating_score
        if candidate.rating is not None and candidate.rating >= 4.5:
            reasons.append(f"rated {candidate.rating:g}/5")
        scored.append((total, candidate, tuple(reasons)))

    scored.sort(key=lambda row: -row[0])
    best = scored[0]
    return Recommendation(
        candidate=best[1],
        reasons=best[2] or ("closest match to your request",),
        runner_up=scored[1][1] if len(scored) > 1 else None,
    )


# Words that describe the act of asking, not the product being asked for.
_REQUEST_NOISE = frozenset(
    """add buy get find need want order please cart list the and for from with some any
    just now can you could would like me my mine another one two get""".split()
)


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _add(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _join(existing: str | None, addition: str) -> str:
    return f"{existing} {addition}" if existing else addition
