"""Tell a command apart from a question.

"Add shampoo to my cart" is an instruction: the user wants a decision made, not a
list to read. "Find me shampoo" or "what shampoo is good" is a request to look, where
showing the options is the helpful answer.

Both are deterministic reads of the user's own wording. Nothing here searches,
decides, or stores anything.
"""

from enum import StrEnum
import re


class RequestMode(StrEnum):
    COMMAND = "command"      # decide for me
    BROWSE = "browse"        # show me the options


# Verbs that instruct rather than enquire. "add ... to cart", "buy", "order", "get me",
# "reorder", "grab", "restock".
COMMAND_VERB = re.compile(
    r"^\s*(?:please\s+)?(?:can you\s+|could you\s+|go ahead and\s+)?"
    r"(?:add|buy|order|reorder|re-order|purchase|grab|restock|get)\b"
)
# Phrases that ask to look rather than to act.
BROWSE_MARKER = re.compile(
    r"\b(?:find|search|show|look for|looking for|browse|what|which|any\b|options|"
    r"compare|recommend|suggest|ideas|see)\b"
)
# "add it to my cart" is still a command even though "cart" appears.
CART_TARGET = re.compile(r"\b(?:to|in|into)\s+(?:my\s+)?(?:cart|basket|list|order)\b")


def classify(message: str) -> RequestMode:
    """Return how the user wants to be served.

    A command verb wins over a browse marker, because "add me the cheapest shampoo
    you can find" is an instruction that happens to contain "find".
    """
    lowered = message.casefold().strip()
    if COMMAND_VERB.match(lowered) or CART_TARGET.search(lowered):
        return RequestMode.COMMAND
    if BROWSE_MARKER.search(lowered):
        return RequestMode.BROWSE
    # A bare product name ("AA batteries") is a request to look, which is the safer
    # default: it shows options instead of choosing on the user's behalf.
    return RequestMode.BROWSE
