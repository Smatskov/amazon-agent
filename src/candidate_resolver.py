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
# A minus sign creates a word boundary, so "-1" used to read as position 1.
BARE_NUMBER = re.compile(r"(?<![\d\-–—])(\d+)\b")
NUMBER_ONLY = re.compile(r"^[-+]?\d+[.!]?$")
CHEAPEST_MARKERS = ("cheapest", "cheaper", "lowest price", "least expensive")
HIGHEST_RATED_MARKERS = ("highest rated", "highest-rated", "best rated", "best reviewed", "best rating")
# Ordinary conversational filler that never identifies a specific product.
STOP_WORDS = frozenset(
    """a an and buy choice choose delete do drop for from get give go i ill im instead it item
    just lets let list me my number of off ok okay one option order pick please remove select
    sure take thanks that the then this to want with yes yeah yep actually cart""".split()
)
PLURAL_ES = ("ses", "xes", "zes", "ches", "shes")


@dataclass(frozen=True, slots=True)
class CandidateResolution:
    candidate: Candidate | None
    message: str | None = None
    # True when several candidates fit equally. An ambiguous reference deserves its own
    # focused question; a reference that matched nothing is better served by the full
    # next-step guidance.
    ambiguous: bool = False


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

    # A message that is only a number is a position attempt and nothing else. Falling
    # through would let "-1" or "9" match a digit inside a product title.
    if NUMBER_ONLY.match(lower):
        return CandidateResolution(
            None,
            f"There are {len(candidates)} options. Reply with a number from 1 to {len(candidates)}.",
        )

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


def _singular(word: str) -> str:
    """Fold a simple plural so "t shirts" can match "T-Shirt"."""
    if word.endswith(PLURAL_ES):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _significant_tokens(text: str) -> set[str]:
    # Numbers of any length are kept because pack counts ("10 count", "3 pack") are
    # how users identify a specific variant.
    return {
        _singular(token)
        for token in re.findall(r"[a-z0-9]+", text)
        if (len(token) >= 3 or token.isdigit()) and token not in STOP_WORDS
    }


# A reference points at something already on screen, so it is short: "the duracell",
# "natrol gummies". A longer phrase is the user naming what they want, which is a
# search. Matching those against stored results is what let "oral-B toothbrushes 6
# pack" silently add a stale result instead of searching Amazon for it.
MAX_REFERENCE_TOKENS = 3


def _described_candidates(lower: str, candidates: list[Candidate]) -> list[Candidate]:
    """Match candidates by the significant words the user actually typed.

    Deliberately conservative in two ways. A message long enough to be a product
    request is never read as a reference, and words that match every candidate equally
    are treated as not discriminating rather than as an ambiguous reference — five
    results all titled "Oral-B" should not turn "oral b toothpaste" into a question
    about which one was meant.
    """
    tokens = _significant_tokens(lower)
    if not tokens or len(tokens) > MAX_REFERENCE_TOKENS:
        return []

    scores = []
    for candidate in candidates:
        haystack = candidate.title.casefold()
        if candidate.brand:
            haystack = f"{haystack} {candidate.brand.casefold()}"
        words = {_singular(word) for word in re.findall(r"[a-z0-9]+", haystack)}
        scores.append((len(tokens & words), candidate))

    best = max(score for score, _ in scores)
    if best == 0:
        return []
    matched = [candidate for score, candidate in scores if score == best]
    # Every candidate fitting equally well means the words separate nothing.
    if len(matched) == len(candidates) and len(candidates) > 1:
        return []
    return matched


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
    return CandidateResolution(
        None,
        f"More than one option matches {label}. Which option do you mean?",
        ambiguous=True,
    )
