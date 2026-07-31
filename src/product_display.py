"""Turn stored facts into Telegram messages.

Output is Telegram HTML: the previous version emitted markdown asterisks that Telegram
sent verbatim, so the user saw literal `**Pick one:**`. Every value that came from
Amazon or the user is escaped, because a product title can contain characters that
would otherwise break the message.

Presentation is separated from orchestration so message shape can change without
touching workflow decisions. This module shortens and arranges facts; it never adds
one, and it never invents a value that Amazon did not supply.
"""

from html import escape
import re

import menu
import ranking
from menu import MenuOption
from ranking import RankedCandidates
from workflow_models import Candidate, CartLine


# Marketing detail is usually appended after a comma, a spaced dash, or a bracket.
SEGMENT_SPLIT = re.compile(r"\s*[,|;]\s*|\s+[-–—]\s+|\s*[()\[\]]\s*")
TITLE_WORD_LIMIT = 10
TOTAL_WORD_BUDGET = 14
VARIANT_SEGMENT_WORD_LIMIT = 3
# Phrases that carry no information for a shopper choosing between options.
FILLER_PHRASES = re.compile(
    r"\b(?:packaging may vary|may vary|new version|frustration[- ]free packaging|"
    r"amazon exclusive|packaging varies)\b",
    re.IGNORECASE,
)
NOT_IN_AMAZON_CART = (
    "Held by me only — nothing is in your Amazon cart yet, and I can't place orders."
)


def text(value: str | None) -> str:
    """Escape anything that came from Amazon or the user."""
    return escape(value or "", quote=False)


def display_title(raw: str, *, word_limit: int = TITLE_WORD_LIMIT) -> str:
    """Shorten a raw Amazon title while keeping the facts that identify the variant.

    Colour, size, and pack count distinguish otherwise identical listings, so they are
    preserved ahead of marketing copy even though they appear later in the title.
    """
    cleaned = FILLER_PHRASES.sub("", raw or "")
    segments = [
        segment.strip()
        for segment in SEGMENT_SPLIT.split(cleaned)
        if segment and segment.strip()
    ]
    if not segments:
        return (raw or "").strip()

    head = _trim(segments[0], word_limit)
    if ranking.pack_count(head):
        return head

    variants = [
        segment for segment in segments[1:]
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
    """A short facts line: price, unit price, arrival.

    Review counts are deliberately absent. They drive the ranking, but printing
    "(24,037 reviews)" beside every option is noise once the user trusts the ordering.
    """
    facts = [candidate.price_text or "price not shown"]
    unit = ranking.unit_price(candidate)
    if unit is not None and (candidate.price or 0) != unit:
        facts.append(f"{unit:.2f} each".replace("0.", "$0.") if unit < 1 else f"${unit:.2f} each")
    if candidate.delivery_label:
        facts.append(f"arrives {candidate.delivery_label}")
    return " · ".join(text(fact) for fact in facts)


def candidate_line(number: int, candidate: Candidate) -> str:
    return (
        f"{number} · <b>{text(display_title(candidate.title))}</b>\n"
        f"    {candidate_facts(candidate)}"
    )


def present_results(
    goal: str,
    ranked: RankedCandidates,
    options: list[MenuOption],
    *,
    removed: int = 0,
    refined: bool = False,
) -> str:
    """Search results, always visually distinct from the user's own list."""
    candidates = ranked.candidates
    if not candidates:
        return f"No results for <b>{text(goal)}</b> that met your requirements."

    lead = "Narrowed to" if refined else "Results for"
    blocks = [f"🔎 <b>{lead} {text(goal)}</b>"]
    blocks.append("\n\n".join(candidate_line(i, c) for i, c in enumerate(candidates, 1)))

    notes = []
    if removed:
        notes.append(f"{removed} result{_plural(removed)} left out.")
    if ranked.caveat:
        notes.append(text(ranked.caveat))
    if notes:
        blocks.append(f"<i>{' '.join(notes)}</i>")

    blocks.append(menu.render(options, start=len(candidates) + 1, heading="Or:"))
    return "\n\n".join(block for block in blocks if block)


def present_recommendation(
    goal: str, recommendation, total_found: int, options: list[MenuOption]
) -> str:
    """One pick with the evidence for it, and the choices that follow."""
    candidate = recommendation.candidate
    blocks = [
        f"🛒 <b>Best match for {text(goal)}</b>",
        f"<b>{text(display_title(candidate.title))}</b>\n{candidate_facts(candidate)}",
        f"<i>{text('; '.join(recommendation.reasons))}</i>",
    ]
    if total_found > 1:
        blocks.append(f"<i>Chosen from {total_found} results.</i>")
    blocks.append(menu.render(options, heading="What next?"))
    return "\n\n".join(blocks)


def present_cart(lines: list[CartLine], subtotal: float | None, options: list[MenuOption]) -> str:
    """The user's own list, clearly not a set of search results."""
    if not lines:
        return "🧺 <b>Your list is empty.</b>\n\nTell me what to look for."

    count = sum(line.quantity for line in lines)
    blocks = [f"🧺 <b>Your list</b> — {count} item{_plural(count)}"]
    blocks.append("\n".join(_cart_line(i, line) for i, line in enumerate(lines, 1)))
    blocks.append(_subtotal_line(subtotal))
    blocks.append(f"<i>{NOT_IN_AMAZON_CART}</i>")
    blocks.append(menu.render(options, heading="What next?"))
    return "\n\n".join(block for block in blocks if block)


def present_checkout(summary, options: list[MenuOption]) -> str:
    """Exactly what the user is being asked to approve."""
    if summary.is_empty:
        return "Nothing to check out — your list is empty."

    blocks = ["📋 <b>Order summary</b>"]
    blocks.append("\n".join(_cart_line(i, line) for i, line in enumerate(summary.lines, 1)))
    blocks.append(_subtotal_line(summary.subtotal))
    blocks.append(
        f"<i>Excludes {text(_sentence_list(list(summary.unknown)))}. "
        "The real total will be higher.</i>"
    )
    blocks.append(menu.render(options, heading="What next?"))
    return "\n\n".join(blocks)


def _cart_line(number: int, line: CartLine) -> str:
    facts = [line.price_text or "price not shown"]
    if line.quantity > 1:
        facts.insert(0, f"×{line.quantity}")
    if line.line_total is not None and line.quantity > 1:
        facts.append(f"= ${line.line_total:.2f}")
    return f"{number} · <b>{text(display_title(line.title))}</b>\n    {text(' · '.join(facts))}"


def _subtotal_line(subtotal: float | None) -> str:
    if subtotal is None:
        return "<b>Subtotal:</b> unavailable — an item showed no price."
    return f"<b>Subtotal:</b> ${subtotal:.2f} <i>(items only)</i>"


def _trim(value: str, word_limit: int) -> str:
    words = value.split()
    return value.strip() if len(words) <= word_limit else " ".join(words[:word_limit]) + "…"


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def _sentence_list(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])}, and {items[-1]}"
