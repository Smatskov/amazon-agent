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


def default_sort(message: str) -> SortPreference:
    """The ordering to use when the user stated no preference.

    Amazon's own order leads with placements and promoted listings, so presenting it
    unchanged meant the first numbered option was regularly the least useful one on the
    page. Once results are relevance-filtered, cheapest-first is both predictable and
    the thing a shopper is usually comparing. An explicit request still wins.
    """
    stated = requested_sort(message)
    return SortPreference.PRICE if stated is SortPreference.RELEVANCE else stated


MAX_PRICE_PHRASE = re.compile(
    r"(?:under|below|less than|cheaper than|max|no more than|up to|<)\s*\$?\s*(\d+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
MIN_PRICE_PHRASE = re.compile(
    r"(?:over|above|more than|at least|min|no less than|starting at|>)\s*\$?\s*(\d+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
# A range, which the budget parser previously could not see at all. "between 10 and 20
# dollars" matched no max-price phrase, so "between" and "dollars" survived as leftover
# words and became a keyword the title had to contain — filtering everything out.
# Each alternative requires its own evidence that a price is meant: the word "between"
# or "from", a dollar sign, or a trailing currency word.
PRICE_RANGE_PHRASES = (
    re.compile(
        r"\b(?:between|from)\s*\$?\s*(\d+(?:\.\d{1,2})?)\s*(?:to|and|through|[-–—])\s*\$?\s*(\d+(?:\.\d{1,2})?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\$\s*(\d+(?:\.\d{1,2})?)\s*(?:to|and|through|[-–—])\s*\$?\s*(\d+(?:\.\d{1,2})?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d+(?:\.\d{1,2})?)\s*(?:to|and|through|[-–—])\s*(\d+(?:\.\d{1,2})?)\s*"
        r"(?:dollars?|bucks?|usd)\b",
        re.IGNORECASE,
    ),
)
MIN_RATING_PHRASE = re.compile(
    r"(?:rated|rating|stars?)\D{0,12}(\d(?:\.\d)?)|(\d(?:\.\d)?)\s*stars?\s*(?:or\s*(?:more|up|better|above))?",
    re.IGNORECASE,
)
PRIME_PHRASE = re.compile(r"\bprime\b", re.IGNORECASE)
CONSTRAINT_NOISE = frozenset(
    """only just the a an and or with under below less than more over cheaper max show me
    ones one option options please product products that is are be by for
    dont don't want need looking""".split()
)
# Words that describe money. They are noise *only* when a price was actually parsed
# out of the message: "between 10 and 20 dollars" leaves "between" and "dollars"
# behind, and treating them as a keyword excluded every product. But "Dollar Shave
# Club" and "Two Buck Chuck" are real things to search for, so these words are never
# discarded when no price constraint was found.
MONEY_WORDS = frozenset(
    """between from through dollars dollar bucks buck usd price prices cost costs
    budget range around about spend pay paying""".split()
)


def parse_constraint(message: str) -> dict:
    """Read a narrowing instruction deterministically.

    "under $20", "between 10 and 20 dollars", "only prime", "4 stars or more", or a
    bare brand word. Anything left over becomes a keyword the title must contain,
    which is how a brand narrows. Words that describe money rather than the product
    are treated as noise, because a leftover "dollars" became a keyword no title
    could satisfy.
    """
    constraints: dict = {}
    lowered = (message or "").casefold()
    consumed = lowered

    low, high, matched = _price_range(lowered)
    if matched is not None:
        constraints["min_price"] = low
        constraints["max_price"] = high
        consumed = consumed.replace(matched, " ")
    else:
        price = MAX_PRICE_PHRASE.search(lowered)
        if price:
            constraints["max_price"] = float(price.group(1))
        floor = MIN_PRICE_PHRASE.search(lowered)
        if floor:
            constraints["min_price"] = float(floor.group(1))
        consumed = MIN_PRICE_PHRASE.sub(" ", MAX_PRICE_PHRASE.sub(" ", consumed))

    if PRIME_PHRASE.search(lowered):
        constraints["prime"] = True
    rating = MIN_RATING_PHRASE.search(lowered)
    if rating and "star" in lowered:
        value = rating.group(1) or rating.group(2)
        if value and 0 < float(value) <= 5:
            constraints["min_rating"] = float(value)

    consumed = PRIME_PHRASE.sub(" ", consumed)
    # Money words are only noise once a price has actually been read out of the
    # message; otherwise "dollar shave club" would lose the word that names it.
    found_a_price = "min_price" in constraints or "max_price" in constraints
    noise = CONSTRAINT_NOISE | MONEY_WORDS if found_a_price else CONSTRAINT_NOISE
    words = [
        word
        for word in re.findall(r"[a-z0-9'&-]{3,}", consumed)
        if word not in noise and not word.isdigit()
    ]
    if words:
        constraints["keyword"] = " ".join(words)
    return constraints


def _price_range(lowered: str) -> tuple[float | None, float | None, str | None]:
    """Read "between X and Y" in any of its written forms, lower bound first."""
    for pattern in PRICE_RANGE_PHRASES:
        match = pattern.search(lowered)
        if match:
            first, second = float(match.group(1)), float(match.group(2))
            return min(first, second), max(first, second), match.group(0)
    return None, None, None


# Words that describe how the search was phrased rather than what is wanted.
QUERY_NOISE = frozenset(
    """the and for with new some any that this get buy need want order find search show
    looking pack count size pcs please just now can you like from""".split()
)


def significant_tokens(text: str) -> set[str]:
    """Fold text to comparable words.

    Splits on anything that is not a letter or digit, so "Oral-B" and "oral b" agree
    and punctuation the user omits cannot change the result. A run like "10mg" is also
    emitted as "10" and "mg" so it matches a title written "10 mg".
    """
    tokens: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", (text or "").casefold()):
        for part in {raw, *re.findall(r"\d+|[a-z]+", raw)}:
            if len(part) >= 3 or part.isdigit():
                tokens.add(_fold(part.replace("'", "")))
    return tokens


def _close_enough(word: str, vocabulary: set[str]) -> bool:
    """Exact match, or a single typo in a word long enough for that to be safe.

    Short words are compared exactly: at four characters or fewer a single edit is as
    likely to be a different word as a misspelling of this one.
    """
    if word in vocabulary:
        return True
    if len(word) < 5:
        return False
    return any(
        abs(len(word) - len(other)) <= 1 and _within_one_edit(word, other)
        for other in vocabulary
    )


def _within_one_edit(a: str, b: str) -> bool:
    """True when one substitution, insertion, or deletion turns a into b."""
    if a == b:
        return True
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    for index in range(len(longer)):
        if longer[:index] + longer[index + 1:] == shorter:
            return True
    return False


def _token_alternatives(text: str) -> list[set[str]]:
    """Group each written word with the forms it could legitimately take.

    "10mg" is one word the user typed but two things a title may say, so it becomes
    the group {"10mg", "10"} and is satisfied by either. Grouping matters: requiring
    every form would make "10mg" fail against a title that writes "10 mg".
    """
    groups: list[set[str]] = []
    for raw in re.findall(r"[a-z0-9]+", (text or "").casefold()):
        forms = {
            _fold(part.replace("'", ""))
            for part in {raw, *re.findall(r"\d+|[a-z]+", raw)}
            if len(part) >= 3 or part.isdigit()
        }
        if forms:
            groups.append(forms)
    return groups


def matches_keyword(title: str, keyword: str) -> bool:
    """Every word of a narrowing phrase must be present, allowing for typos.

    "Nature's Bounty" narrowed against "Nature Made" must fail: requiring all words
    means the shared word "nature" is not enough, which is what stops a narrowing from
    quietly returning a different brand.
    """
    groups = _token_alternatives(keyword)
    if not groups:
        return True
    present = significant_tokens(title)
    return all(
        any(_close_enough(form, present) for form in group) for group in groups
    )


def relevance(candidates: list[Candidate], query: str) -> FilterOutcome:
    """Drop results that share no meaningful word with what was asked for.

    Amazon mixes placements into organic results that carry a real ASIN, a real price,
    and no sponsored marker of any kind -- a "One Medical Membership, $99.00" appeared
    among melatonin results and was selectable as option 1. Marker-based ad detection
    was verified live to be useless here (the genuine products carried the sponsored
    marker and the placement did not), so relevance to the query is the only honest
    discriminator available.
    """
    wanted = {word for word in significant_tokens(query) if word not in QUERY_NOISE}
    if not wanted:
        return FilterOutcome(list(candidates), 0)

    kept = [
        candidate
        for candidate in candidates
        if significant_tokens(candidate.title) & wanted
    ]
    removed = len(candidates) - len(kept)
    # Nothing sharing a single word with the request means the query itself was not
    # understood as a product. Showing the results anyway produced "A Smell of Honey,
    # $19.99" for "6 dont want to pay over 10 bucks" — a real listing, and garbage as
    # an answer. Reporting nothing lets the caller say so plainly instead.
    return FilterOutcome(kept, removed, ("unrelated to your search",) if removed else ())


def apply_constraints(
    candidates: list[Candidate], constraints: dict | None
) -> FilterOutcome:
    """Drop candidates that violate a stated hard constraint; ignore unknown keys."""
    if not constraints:
        return FilterOutcome(list(candidates), 0)

    max_price = _number(constraints.get("max_price"))
    min_price = _number(constraints.get("min_price"))
    min_rating = _number(constraints.get("min_rating"))
    prime_required = constraints.get("prime") is True or constraints.get("prime_required") is True
    keyword = constraints.get("keyword")

    kept: list[Candidate] = []
    reasons: list[str] = []
    for candidate in candidates:
        if keyword:
            if not matches_keyword(candidate.title, str(keyword)):
                _add(reasons, f"not matching '{keyword}'")
                continue
        # A missing fact is not a violation; it cannot prove the constraint is broken.
        if max_price is not None and candidate.price is not None and candidate.price > max_price:
            _add(reasons, f"over ${max_price:g}")
            continue
        if min_price is not None and candidate.price is not None and candidate.price < min_price:
            _add(reasons, f"under ${min_price:g}")
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


# Size words, largest first. "pro max" must be tested before "pro" or it never matches.
VARIANT_SIZE_RANK = (("pro max", 5), ("promax", 5), ("plus", 4), ("max", 4),
                     ("pro", 3), ("mini", 1), ("se", 1))
DEFAULT_SIZE_RANK = 2


def sort_variants(rows: list[list], described_asin: str | None = None) -> list[list]:
    """Order the versions of one product so the list can be read rather than scanned.

    Applies **only** to the variation picker. The search-results ordering is decided by
    `rank()` and is deliberately untouched: those are different products competing on
    price, while these are one product's versions competing on which the user owns.

    Order: the version the search result actually described comes first, because that
    is the one whose price and picture the user just looked at. The rest go newest
    model first, then largest size, then colour — so an iPhone 17 Pro case is never
    buried under a case for an iPhone 8.
    """
    def key(row: list) -> tuple:
        asin = row[0] if row else ""
        label = row[1] if len(row) > 1 else ""
        parts = [part.strip() for part in label.split("·") if part.strip()]
        tail = parts[-1] if parts else label
        head = " ".join(parts[:-1]) if len(parts) > 1 else ""
        # A stated pack size wins over any other number in the value: "3.8 Ounce
        # (Pack of 3)" is a bigger buy than "(Pack of 1)", and taking the largest
        # number would compare the ounces instead and call them equal.
        pack = pack_count(tail)
        numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", tail)]
        model = float(pack) if pack else (max(numbers) if numbers else 0.0)
        lowered = tail.casefold()
        size = next((rank for word, rank in VARIANT_SIZE_RANK if word in lowered), DEFAULT_SIZE_RANK)
        return (
            0 if described_asin and asin == described_asin else 1,
            -model,
            -size,
            head.casefold(),
            tail.casefold(),
        )

    return sorted(rows, key=key)


def _fold(word: str) -> str:
    """Fold a simple plural so "tablets" and "tablet" compare equal."""
    if word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _add(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _join(existing: str | None, addition: str) -> str:
    return f"{existing} {addition}" if existing else addition
