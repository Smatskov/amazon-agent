"""Resolve user references against stored Amazon candidates without guessing."""

from dataclasses import dataclass
import re

from workflow_models import Candidate


# Position words the user may type instead of a displayed option number.
ORDINAL_WORDS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
    "last": -1,
}
# An explicit reference such as "option 5", "number 5", "#5", or "no. 5".
EXPLICIT_POSITION = re.compile(r"\b(?:option|number|no\.?|choice|item)\s*#?\s*(\d+)\b|#\s*(\d+)\b")
BARE_NUMBER = re.compile(r"\b(\d+)\b")
CHEAPEST_MARKERS = ("cheapest", "cheaper", "lowest price", "least expensive")
HIGHEST_RATED_MARKERS = ("highest rated", "highest-rated", "best rated", "best reviewed", "best rating")
# Ordinary conversational filler that never identifies a specific product.
STOP_WORDS = frozenset(
    """a an and buy choice choose do for get give go i ill im it item just lets let me my number
    of ok okay one option order pick please select sure take thanks that the then this to want
    with yes yeah yep""".split()
)


@dataclass(frozen=True, slots=True)
class CandidateResolution:
    candidate: Candidate | None
    message: str | None = None


def resolve_candidate_reference(message: str, candidates: list[Candidate]) -> CandidateResolution:
    """Select exactly one candidate or return a focused ambiguity/no-match response."""
    if not candidates:
        return CandidateResolution(None, "There are no candidates to select yet.")

    lower = message.casefold().strip()

    if any(marker in lower for marker in CHEAPEST_MARKERS):
        return _unique_extreme(candidates, lambda candidate: candidate.price, "the cheapest option")
    if any(marker in lower for marker in HIGHEST_RATED_MARKERS):
        return _unique_extreme(
            candidates,
            lambda candidate: None if candidate.rating is None else -candidate.rating,
            "the highest rated option",
        )

    # An explicit position always wins over a product word so "option 2" is never
    # reinterpreted as a description.
    position = explicit_position(lower, len(candidates))
    if position is not None:
        return CandidateResolution(candidates[position - 1])

    described = _described_candidates(lower, candidates)
    if described:
        return _from_matches(described, "that description")

    # A bare number is the weakest signal, so it is used only when nothing in the
    # message describes a product.
    position = _bare_position(lower, len(candidates))
    if position is not None:
        return CandidateResolution(candidates[position - 1])

    return CandidateResolution(
        None,
        "I couldn't tell which option you meant. "
        f"Reply with a number from 1 to {len(candidates)}, or name the product.",
    )


def _position_in_range(value: int, count: int) -> int | None:
    return value if 1 <= value <= count else None


def explicit_position(lower: str, count: int) -> int | None:
    """Read a position the user stated explicitly, by keyword or ordinal word.

    Shared with `workflow_reply` so the deterministic fast path and the full resolver
    can never disagree about what "option 3" means.
    """
    match = EXPLICIT_POSITION.search(lower)
    if match:
        return _position_in_range(int(match.group(1) or match.group(2)), count)
    for word, position in ORDINAL_WORDS.items():
        if re.search(rf"\b{word}\b", lower):
            # "last" is only meaningful when there is a list to be last in.
            return _position_in_range(count if position == -1 else position, count)
    # A message that is only a number is an explicit choice, not a description.
    if lower.rstrip(".!").isdigit():
        return _position_in_range(int(lower.rstrip(".!")), count)
    return None


def _bare_position(lower: str, count: int) -> int | None:
    numbers = [int(value) for value in BARE_NUMBER.findall(lower)]
    valid = {number for number in numbers if 1 <= number <= count}
    return valid.pop() if len(valid) == 1 else None


def _described_candidates(lower: str, candidates: list[Candidate]) -> list[Candidate]:
    """Match candidates by the significant words the user actually typed."""
    # Numbers of any length are kept because pack counts ("10 count", "3 pack") are
    # how users identify a specific variant.
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", lower)
        if (len(token) >= 3 or token.isdigit()) and token not in STOP_WORDS
    }
    if not tokens:
        return []

    scores = []
    for candidate in candidates:
        haystack = candidate.title.casefold()
        if candidate.brand:
            haystack = f"{haystack} {candidate.brand.casefold()}"
        words = set(re.findall(r"[a-z0-9]+", haystack))
        scores.append((len(tokens & words), candidate))

    best = max(score for score, _ in scores)
    return [] if best == 0 else [candidate for score, candidate in scores if score == best]


def _unique_extreme(candidates: list[Candidate], key, label: str) -> CandidateResolution:
    """Compare only candidates that carry the fact being compared."""
    ranked = [(key(candidate), candidate) for candidate in candidates]
    ranked = [(value, candidate) for value, candidate in ranked if value is not None]
    if not ranked:
        return CandidateResolution(None, f"I don't have the information needed to pick {label}.")
    best = min(value for value, _ in ranked)
    return _from_matches([candidate for value, candidate in ranked if value == best], label)


def _from_matches(matches: list[Candidate], label: str) -> CandidateResolution:
    if len(matches) == 1:
        return CandidateResolution(matches[0])
    if not matches:
        return CandidateResolution(None, f"I couldn't match {label}. Please name an option or number.")
    return CandidateResolution(None, f"More than one option matches {label}. Which option do you mean?")
