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
# A leading segment this short is a brand, not a product name. Amazon separates with
# "|", so "Dollar Shave Club | Shave Gel 6.7 ounce (2 Pack) | ..." split into a brand
# head and descriptive segments too long to keep — leaving five results all reading
# "Dollar Shave Club, 2 Pack". The name keeps absorbing segments until it says what
# the product actually is.
MIN_NAME_WORDS = 4
# A segment with no letters or digits carries no information: an Amazon title ending
# in an ellipsis produced a displayed name ending ", ...".
HAS_CONTENT = re.compile(r"[a-z0-9]", re.IGNORECASE)
# Phrases that carry no information for a shopper choosing between options.
FILLER_PHRASES = re.compile(
    r"\b(?:packaging may vary|may vary|new version|frustration[- ]free packaging|"
    r"amazon exclusive|packaging varies)\b",
    re.IGNORECASE,
)
# A size or count is what separates two listings of the same product, so it is kept
# ahead of marketing copy even when it appears late in the title.
SIZE_SEGMENT = re.compile(
    r"\d[\d.,]*\s*(?:fl\s*oz|oz|ml|liters?|l|kg|g|lbs?|ct|count|packs?|pk|"
    r"tablets?|gummies|capsules?|softgels?|sheets?|rolls?)\b",
    re.IGNORECASE,
)
# Words that cannot end a shortened title without looking like damage.
DANGLING_WORDS = frozenset(
    "for with and the to in of a an by on at is or from your our plus".split()
)
# Deliberately says nothing about what else the Amazon cart contains: it claimed the
# cart was empty while four unrelated items were sitting in it.
NOT_IN_AMAZON_CART = (
    "This list is held here only — these items have not been sent to your Amazon cart yet."
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
        if segment and segment.strip() and HAS_CONTENT.search(segment)
    ]
    if not segments:
        return (raw or "").strip()

    # Absorb following segments while the name is still too short to identify the
    # product, so a brand alone is never the whole displayed name.
    name = [segments[0]]
    used = len(segments[0].split())
    index = 1
    while index < len(segments) and used < MIN_NAME_WORDS:
        name.append(segments[index])
        used += len(segments[index].split())
        index += 1

    head = _trim(", ".join(name), word_limit)
    variants = [
        segment for segment in segments[index:]
        if len(segment.split()) <= VARIANT_SEGMENT_WORD_LIMIT
    ]
    size_segment = next((segment for segment in variants if SIZE_SEGMENT.search(segment)), None)

    if ranking.pack_count(head):
        # The head already names the pack, but a size still separates two listings of
        # the same pack: "Dove Body Wash 2-Pack" alone dropped "15.2 Oz Ea".
        return f"{head}, {size_segment}" if size_segment else head

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
    """A short facts line: price, per-unit price, arrival.

    Review counts are deliberately absent. They drive the ranking, but printing
    "(24,037 reviews)" beside every option is noise once the user trusts the ordering.

    The per-unit price is what makes sizes comparable — a $15.88 7oz bottle beside a
    $25.55 twin pack tells the user nothing until both are stated per ounce. Amazon's
    own figure is preferred; the pack-count division is a fallback.
    """
    facts = [candidate.price_text or "price not shown", _pack_fact(candidate)]
    if candidate.unit_price_text:
        facts.append(candidate.unit_price_text)
    else:
        unit = ranking.unit_price(candidate)
        if unit is not None and (candidate.price or 0) != unit:
            facts.append(f"{unit:.2f} each".replace("0.", "$0.") if unit < 1 else f"${unit:.2f} each")
    if candidate.delivery_label:
        facts.append(f"arrives {candidate.delivery_label}")
    return " · ".join(text(fact) for fact in facts if fact)


def _pack_fact(candidate: Candidate) -> str:
    """State the pack size when Amazon gave one, and say nothing when it did not.

    An absent count is the common case, so warning about it on most lines was noise
    rather than signal. The variation picker is what actually protects the user here:
    a listing whose count is chosen on the product page now asks which one before
    anything is added (ADR-058).
    """
    count = ranking.pack_count(candidate.title)
    return f"{count} in the pack" if count else ""


def candidate_line(number: int, candidate: Candidate, *, note: str = "") -> str:
    suffix = f"  <i>{text(note)}</i>" if note else ""
    return (
        f"{number} · <b>{text(display_title(candidate.title))}</b>{suffix}\n"
        f"    {candidate_facts(candidate)}"
    )


def _outlier_notes(candidates: list[Candidate]) -> dict[str, str]:
    """Flag a result that costs far more per unit than the rest of the set.

    A $15.88 medicated shampoo sitting among $6 bottles is not a mistake, but nothing
    on the line explained why it cost three times as much. Comparing against the
    median rather than the mean keeps one outlier from hiding another.
    """
    values = {}
    for candidate in candidates:
        unit = _comparable_unit(candidate)
        if unit is not None:
            values[candidate.candidate_id] = unit
    if len(values) < 3:
        return {}
    ordered = sorted(values.values())
    median = ordered[len(ordered) // 2]
    if median <= 0:
        return {}
    return {
        candidate_id: "· specialist or premium — costs well above the rest per unit"
        for candidate_id, unit in values.items()
        if unit >= median * 2
    }


def _comparable_unit(candidate: Candidate) -> float | None:
    """A per-unit number for comparison, from Amazon's own text where it exists."""
    if candidate.unit_price_text:
        match = re.search(r"\$\s*([\d,]+\.?\d*)", candidate.unit_price_text)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                return None
    return ranking.unit_price(candidate)


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

    lead = "NARROWED TO" if refined else "RESULTS FOR"
    blocks = [f"🔎 <b>{lead} {text(goal.upper())}</b>"]
    outliers = _outlier_notes(candidates)
    blocks.append("\n\n".join(
        candidate_line(i, c, note=outliers.get(c.candidate_id, ""))
        for i, c in enumerate(candidates, 1)
    ))

    footnotes = []
    if removed:
        footnotes.append(f"{removed} result{_plural(removed)} left out.")
    if ranked.caveat:
        footnotes.append(text(ranked.caveat))
    if footnotes:
        blocks.append(f"<i>{' '.join(footnotes)}</i>")

    blocks.append(menu.render(options, start=len(candidates) + 1, heading="Or:"))
    return "\n\n".join(block for block in blocks if block)


def present_cart(lines: list[CartLine], subtotal: float | None, options: list[MenuOption]) -> str:
    """The user's own list, clearly not a set of search results."""
    if not lines:
        return "🧺 <b>YOUR LIST IS EMPTY</b>\n\nTell me what to look for."

    count = sum(line.quantity for line in lines)
    blocks = [f"🧺 <b>YOUR LIST</b> — {count} item{_plural(count)}"]
    blocks.append("\n".join(_cart_line(i, line) for i, line in enumerate(lines, 1)))
    blocks.append(_subtotal_line(subtotal))
    blocks.append(f"<i>{NOT_IN_AMAZON_CART}</i>")
    blocks.append(menu.render(options, heading="What next?"))
    return "\n\n".join(block for block in blocks if block)


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
    """Shorten to a word budget without ending on a word that leads nowhere.

    Cutting at a fixed count produced "Dove Body Wash with Pump 3 Count Deep Moisture
    for…", where the trailing "for" reads as though the name was damaged rather than
    shortened.
    """
    words = value.split()
    if len(words) <= word_limit:
        return value.strip()
    kept = words[:word_limit]
    while kept and kept[-1].casefold().strip(",.;-") in DANGLING_WORDS:
        kept.pop()
    return " ".join(kept) + "…" if kept else " ".join(words[:word_limit]) + "…"


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def _sentence_list(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _money(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\d[\d,]*(?:\.\d{1,2})?", value)
    try:
        return float(match.group().replace(",", "")) if match else None
    except ValueError:
        return None


def _cart_blocks(amazon_cart: list) -> tuple[list[str], str, int]:
    """Render the whole Amazon cart, separating what this conversation added.

    Both totals matter and only one was ever shown. The order the user would place is
    every line here, so the count and total come from the cart, not from the list.
    """
    if not amazon_cart:
        return [], "", 0
    mine = [row for row in amazon_cart if len(row) > 2 and row[2]]
    theirs = [row for row in amazon_cart if not (len(row) > 2 and row[2])]
    amounts = [_money(row[1]) for row in amazon_cart]
    total = (
        f"${sum(a for a in amounts if a is not None):.2f}"
        if amounts and all(a is not None for a in amounts)
        else "unknown — an item showed no price"
    )

    def lines(rows: list) -> str:
        return "\n".join(
            f"• <b>{text(display_title(row[0]))}</b>"
            f"{'  ' + text(row[1]) if row[1] else ''}"
            for row in rows
        )

    blocks = []
    if mine:
        blocks.append(f"🆕 <b>ADDED FROM YOUR LIST</b> — {len(mine)} item(s)\n{lines(mine)}")
    if theirs:
        blocks.append(
            f"📦 <b>ALREADY IN YOUR CART</b> — {len(theirs)} item(s)\n"
            f"<i>Not added here. These would be ordered too.</i>\n{lines(theirs)}"
        )
    return blocks, total, len(amazon_cart)


def _destination_block(destination) -> str:
    """Where it ships and what would pay, as Amazon reports them.

    Both are read-only and both say so. The agent cannot change either: Amazon puts
    the address book behind a fresh sign-in, and this application never authenticates
    or touches payment details. Showing them without that caveat would imply a control
    the user does not have here.
    """
    if not destination:
        return ""
    address = (destination[0] if len(destination) > 0 else None) or "could not read it"
    card = (destination[1] if len(destination) > 1 else None) or "could not read it"
    return (
        "📍 <b>SHIPPING &amp; CARD</b>\n"
        f"    <b>Ships to</b>  {text(address)}\n"
        f"    <b>Pays with</b>  {text(card)}\n"
        "    <i>Your Amazon defaults, shown read-only. Change them on Amazon before "
        "ordering.</i>"
    )


def present_ready_to_order(
    summary, transfer: str, options: list[MenuOption], amazon_cart: list | None = None,
    destination=None,
) -> str:
    """The screen that stands in for Amazon's own review-your-order page."""
    blocks, total, count = _cart_blocks(amazon_cart or [])
    if not blocks:
        subtotal = "unknown" if summary.subtotal is None else f"${summary.subtotal:.2f}"
        blocks, total, count = [transfer], subtotal, summary.item_count

    head = [f"🛒 <b>IN YOUR AMAZON CART</b> — {count} item(s), {total}"]
    tail = [
        _destination_block(destination),
        f"<i>Excludes {text(_sentence_list(list(summary.unknown)))}. "
        "The real total will be higher.</i>",
        menu.render(options, heading="What next?"),
    ]
    return "\n\n".join(block for block in [*head, *blocks, *tail] if block)


def present_order_placed(
    summary, options: list[MenuOption], amazon_cart: list | None = None, destination=None,
    order_id: str | None = None, order_url: str | None = None,
) -> str:
    """Amazon accepted the order. Everything here is a fact Amazon returned."""
    blocks, total, count = _cart_blocks(amazon_cart or [])
    if not blocks:
        total = "unknown" if summary.subtotal is None else f"${summary.subtotal:.2f}"
        count = summary.item_count
        blocks = ["\n".join(_cart_line(i, line) for i, line in enumerate(summary.lines, 1))]

    reference = f"\n<b>Order number:</b> <code>{text(order_id)}</code>" if order_id else ""
    link = (
        f'\n<a href="{text(order_url)}">Open this order on Amazon</a>'
        if order_url else ""
    )
    return "\n\n".join(block for block in [
        f"✅ <b>ORDER PLACED</b> — {count} item(s), {total}{reference}{link}",
        *blocks,
        _destination_block(destination),
        "<i>Your list has been cleared because the order went through.</i>",
        menu.render(options, heading="What next?"),
    ] if block)


def present_order_failed(
    summary, options: list[MenuOption], detail: str | None, *,
    needs_sign_in: bool = False, declined: bool = False,
    needs_card_verification: bool = False,
) -> str:
    """The order did not happen, and the list is untouched.

    The headline names the cause, because "something went wrong" gives the user
    nothing to act on. The list is stated as intact so nobody re-adds items they
    still have.
    """
    if needs_card_verification:
        headline = "💳 <b>AMAZON WANTS YOUR CARD VERIFIED — NOTHING WAS ORDERED</b>"
        guidance = (
            "Amazon is asking for the full card number to be re-entered before it will "
            "accept an order with that card. <b>I never type card numbers</b>, so this "
            "one is yours: open your cart on Amazon, verify the card there, and then "
            "check out again here. It is a one-time step per card, not something you "
            "will have to repeat for every order."
        )
    elif declined:
        headline = "❌ <b>PAYMENT DECLINED — NOTHING WAS ORDERED</b>"
        guidance = (
            "Amazon would not accept the card on file. Update your payment method on "
            "Amazon, then come back and check out again."
        )
    elif needs_sign_in:
        headline = "🔒 <b>AMAZON WANTS YOU TO SIGN IN — NOTHING WAS ORDERED</b>"
        guidance = (
            "Amazon asks for your password again before accepting an order. I never "
            "enter passwords, so this one is yours to do: sign in on Amazon, then "
            "check out again here within a few minutes."
        )
    else:
        headline = "❌ <b>THE ORDER DID NOT GO THROUGH</b>"
        guidance = "Nothing was bought and nothing was charged."

    return "\n\n".join(block for block in [
        headline,
        f"<i>{text(detail)}</i>" if detail else "",
        guidance,
        "<b>Your list is untouched</b> — the items are still here and still in your "
        "Amazon cart. Nothing was removed.",
        menu.render(options, heading="What next?"),
    ] if block)
