"""Turn stored candidates into concise, fact-preserving Telegram text.

Presentation is separated from orchestration so message shape can change without
touching workflow decisions. Every value shown here comes from a stored candidate:
this module shortens and arranges facts, and never adds one.
"""

import re

import ranking
from ranking import RankedCandidates
from workflow_models import Candidate, CartLine


# Marketing detail is usually appended after a comma, a spaced dash, or a bracket.
SEGMENT_SPLIT = re.compile(r"\s*[,|;]\s*|\s+[-–—]\s+|\s*[()\[\]]\s*")
TITLE_WORD_LIMIT = 12
TOTAL_WORD_BUDGET = 16
# Variant facts are terse ("White", "Medium", "3 Pack", "4 Ounces"); marketing copy
# runs long ("to Reinforce and Protect Enamel"). Length separates them well enough.
VARIANT_SEGMENT_WORD_LIMIT = 3
PREVIEW_DISCLAIMER = "These are search results, not a recommendation or a purchase."
# The basket lives in this agent's database, not in the user's Amazon account. Saying
# so on every cart message prevents the most damaging possible misunderstanding.
NOT_IN_AMAZON_CART = (
    "This list is held by me only — nothing has been added to your Amazon cart, "
    "and no order can be placed."
)


def display_title(raw: str, *, word_limit: int = TITLE_WORD_LIMIT) -> str:
    """Shorten a raw Amazon title while keeping the facts that identify the variant.

    Colour, size, and pack count are what distinguish two otherwise identical
    listings, so they are preserved ahead of marketing copy even though they appear
    later in the title.
    """
    segments = [segment.strip() for segment in SEGMENT_SPLIT.split(raw) if segment and segment.strip()]
    if not segments:
        return raw.strip()

    head = _trim(segments[0], word_limit)
    if ranking.pack_count(head):
        return head

    variants = [
        segment
        for segment in segments[1:]
        if len(segment.split()) <= VARIANT_SEGMENT_WORD_LIMIT
    ]
    pack_segment = next((segment for segment in variants if ranking.pack_count(segment)), None)

    kept: list[str] = []
    used = len(head.split()) + (len(pack_segment.split()) if pack_segment else 0)
    for segment in variants:
        if segment is pack_segment:
            continue
        length = len(segment.split())
        if used + length > TOTAL_WORD_BUDGET:
            break
        kept.append(segment)
        used += length
    if pack_segment:
        kept.append(pack_segment)

    return ", ".join([head, *kept]) if kept else head


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
    if candidate.delivery_label:
        facts.append(f"arrives {candidate.delivery_label}")
    if candidate.prime_eligible:
        facts.append("Prime")
    return " — ".join(facts)


def next_step_hint(candidates: list[Candidate]) -> str:
    """Say what to type, using this list's own facts as the examples.

    The previous line offered "cheapest" and "highest rated", which read as jargon and
    gave no way to narrow the search. Concrete examples drawn from the results are
    easier to act on: a brand that is actually present, a price the results straddle.
    """
    if not candidates:
        return 'Search for something, or say "cancel".'

    lines = [
        f"**Pick one:** reply {'1' if len(candidates) == 1 else f'1–{len(candidates)}'} "
        "to add it to your list."
    ]

    narrow = []
    # With a single result there is nothing to narrow, so only offer a fresh search.
    if len(candidates) > 1:
        brand = _example_brand(candidates)
        if brand:
            narrow.append(f'a brand, like "{brand}"')
        budget = _example_budget(candidates)
        if budget:
            narrow.append(f'a budget, like "under ${budget}"')
    narrow.append("or just name what you want instead")
    lines.append(f"**Narrow it:** type {_sentence_list_plain(narrow)}.")

    lines.append('**Or:** "cancel" to stop.')
    return "\n".join(lines)


def _example_brand(candidates: list[Candidate]) -> str | None:
    """Use a brand that is genuinely in these results, so the example always works."""
    for candidate in candidates:
        first = candidate.title.split()
        if first and first[0].isalpha() and len(first[0]) > 2:
            return first[0]
    return None


def _example_budget(candidates: list[Candidate]) -> str | None:
    """Suggest a threshold that would actually exclude something."""
    prices = sorted(c.price for c in candidates if c.price is not None)
    if len(prices) < 2 or prices[0] == prices[-1]:
        return None
    middle = prices[len(prices) // 2]
    return f"{int(middle)}" if middle >= 2 else None


def _sentence_list_plain(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])}, {items[-1]}"


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


def present_recommendation(goal: str, recommendation, total_found: int) -> str:
    """Name one pick and why, and ask before adding anything."""
    candidate = recommendation.candidate
    reasons = "; ".join(recommendation.reasons)
    blocks = [
        f'For "{goal}" I\'d pick this out of {total_found} result'
        f'{_plural(total_found)}:',
        f"{display_title(candidate.title)}\n{candidate_facts(candidate)}",
        f"Why: {reasons}.",
    ]
    if recommendation.runner_up is not None:
        blocks.append(
            f"Runner-up: {display_title(recommendation.runner_up.title)} — "
            f"{candidate_facts(recommendation.runner_up)}"
        )
    blocks.append(
        'Reply "yes" to add it to your list, "options" to see everything I found, '
        'or name something else.'
    )
    return "\n\n".join(blocks)


def present_cart(lines: list[CartLine], subtotal: float | None) -> str:
    """Show the preview basket, always stating that Amazon has not been touched."""
    if not lines:
        return "Your list is empty. Search for something and pick an option to add it."

    blocks = [f"Your list ({sum(line.quantity for line in lines)} item{_plural(sum(line.quantity for line in lines))}):"]
    for index, line in enumerate(lines, start=1):
        blocks.append(f"{index}. {display_title(line.title)}\n{_line_facts(line)}")
    blocks.append(_subtotal_line(subtotal))
    blocks.append(NOT_IN_AMAZON_CART)
    return "\n\n".join(blocks)


def present_checkout(summary) -> str:
    """The exact contents the user is being asked to approve."""
    if summary.is_empty:
        return "There is nothing to check out. Your list is empty."

    blocks = ["Order summary — please review before confirming."]
    for index, line in enumerate(summary.lines, start=1):
        blocks.append(f"{index}. {display_title(line.title)}\n{_line_facts(line)}")
    blocks.append(_subtotal_line(summary.subtotal))
    blocks.append(
        "Not included, because Amazon has not supplied them: "
        f"{_sentence_list(list(summary.unknown))}. The real total will be higher."
    )
    blocks.append(
        f"{NOT_IN_AMAZON_CART}\n"
        "Reply \"confirm\" to approve this summary, or \"cancel\" to stop."
    )
    return "\n\n".join(blocks)


def _line_facts(line: CartLine) -> str:
    facts = [f"Qty {line.quantity}", line.price_text or "price not shown"]
    if line.line_total is not None and line.quantity > 1:
        facts.append(f"line total ${line.line_total:.2f}")
    return " — ".join(facts)


def _subtotal_line(subtotal: float | None) -> str:
    if subtotal is None:
        return "Subtotal: not available, because at least one item did not show a price."
    return f"Subtotal: ${subtotal:.2f} (items only)"


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
