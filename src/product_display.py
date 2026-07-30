"""Turn stored candidates into concise, fact-preserving Telegram text.

Presentation is separated from orchestration so message shape can change without
touching workflow decisions. Every value shown here comes from a stored candidate:
this module shortens and arranges facts, and never adds one.
"""

import re

import ranking
from ranking import RankedCandidates
from workflow_models import Candidate


# Marketing detail is usually appended after a comma, a spaced dash, or a bracket.
SEGMENT_SPLIT = re.compile(r"\s*[,|;]\s*|\s+[-–—]\s+|\s*[()\[\]]\s*")
TITLE_WORD_LIMIT = 9
PACK_SEGMENT_WORD_LIMIT = 4
PREVIEW_DISCLAIMER = "These are search results, not a recommendation or a purchase."


def display_title(raw: str, *, word_limit: int = TITLE_WORD_LIMIT) -> str:
    """Shorten a raw Amazon title while keeping the brand, product, and pack size."""
    segments = [segment.strip() for segment in SEGMENT_SPLIT.split(raw) if segment and segment.strip()]
    if not segments:
        return raw.strip()

    head = _trim(segments[0], word_limit)
    if ranking.pack_count(head):
        return head
    # A pack size is the fact most likely to be pushed past the first comma, and it is
    # exactly what distinguishes two otherwise identical results.
    for segment in segments[1:]:
        if ranking.pack_count(segment):
            return f"{head}, {_trim(segment, PACK_SEGMENT_WORD_LIMIT)}"
    return head


def candidate_facts(candidate: Candidate) -> str:
    """One line of only the facts Amazon actually showed for this candidate."""
    facts = [candidate.price_text or "price not shown"]
    unit = ranking.unit_price(candidate)
    if unit is not None:
        facts.append(f"${unit:.2f} each")
    if candidate.rating is not None:
        rating = f"{candidate.rating:g}/5"
        if candidate.review_count is not None:
            rating += f" ({candidate.review_count:,} reviews)"
        facts.append(rating)
    if candidate.prime_eligible:
        facts.append("Prime")
    return " — ".join(facts)


def next_step_hint(candidates: list[Candidate]) -> str:
    """Offer only the replies that make sense for these specific candidates."""
    options = []
    if len(candidates) > 1:
        options.append(f"a number from 1 to {len(candidates)}")
    else:
        options.append('"yes" to choose it')
    if sum(1 for candidate in candidates if candidate.price is not None) > 1:
        options.append('"cheapest"')
    if sum(1 for candidate in candidates if candidate.rating is not None) > 1:
        options.append('"highest rated"')
    options.append('"cancel"')
    return f"Reply with {_sentence_list(options)}."


def present_candidates(
    goal: str,
    ranked: RankedCandidates,
    *,
    removed: int = 0,
    removal_reasons: tuple[str, ...] = (),
    refined: bool = False,
) -> str:
    """Build the complete candidate message, spaced so it is readable on a phone."""
    candidates = ranked.candidates
    if not candidates:
        return f'I could not find any Amazon results for "{goal}" that met your requirements.'

    blocks = [_header(goal, ranked, refined)]
    for index, candidate in enumerate(candidates, start=1):
        blocks.append(f"{index}. {display_title(candidate.title)}\n{candidate_facts(candidate)}")

    notes = []
    if removed:
        reasons = f" ({_sentence_list(list(removal_reasons))})" if removal_reasons else ""
        notes.append(f"I left out {removed} result{_plural(removed)}{reasons}.")
    if ranked.caveat:
        notes.append(ranked.caveat)
    if notes:
        blocks.append(" ".join(notes))

    blocks.append(f"{PREVIEW_DISCLAIMER}\n{next_step_hint(candidates)}")
    return "\n\n".join(blocks)


def _header(goal: str, ranked: RankedCandidates, refined: bool) -> str:
    count = len(ranked.candidates)
    # A refinement reuses results already retrieved, so it must not claim a new search.
    lead = "Narrowed to" if refined else "I found"
    found = f'{lead} {count} Amazon result{_plural(count)} for "{goal}"'
    if ranked.basis in ranking.POSITIONAL_BASES:
        return f"{found}, in {ranked.basis}."
    return f"{found}, ordered by {ranked.basis}."


def _trim(text: str, word_limit: int) -> str:
    words = text.split()
    return text.strip() if len(words) <= word_limit else " ".join(words[:word_limit]) + "…"


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def _sentence_list(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])}, or {items[-1]}"
