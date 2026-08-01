"""Numbered choices the user picks from, instead of the agent guessing.

Every reply the agent sends ends with a numbered menu, and every entry maps to one
deterministic action. A reply of "2" cannot be misread, so the classes of bug that
came from free text meaning different things in different places disappear: the same
word no longer selects in one state and narrows in another.

The menu is persisted with the workflow, so the number the user sees is the number the
agent resolves against even after a restart. Nothing here searches, stores, or decides
anything; it renders choices and reads one back.
"""

from dataclasses import dataclass
from html import escape
from enum import StrEnum
import re
from typing import Any


class MenuAction(StrEnum):
    SELECT = "select"            # payload: candidate_id
    SHOW_OPTIONS = "show_options"
    NARROW = "narrow"
    NEW_SEARCH = "new_search"
    VIEW_LIST = "view_list"
    REMOVE = "remove"            # payload: candidate_id, or None to ask which
    CHECKOUT = "checkout"
    CONFIRM = "confirm"
    KEEP_SHOPPING = "keep_shopping"
    PLACE_ORDER = "place_order"
    CHOOSE_VARIANT = "choose_variant"   # payload: child ASIN
    SET_QUANTITY = "set_quantity"       # payload: the quantity, or None to ask
    CANCEL = "cancel"


# A reply that is only a number, tolerating the punctuation people actually type.
NUMERIC_REPLY = re.compile(r"^\s*[#(]?\s*(\d{1,2})\s*[).!]?\s*$")


@dataclass(frozen=True, slots=True)
class MenuOption:
    action: MenuAction
    label: str
    payload: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {"action": self.action.value, "label": self.label, "payload": self.payload}

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "MenuOption | None":
        """Rebuild a stored option, or None when its action no longer exists.

        Menus outlive the code that wrote them. ADR-041 made workflow *fields*
        tolerant but left actions strict, so retiring one would raise inside
        `get_active_workflow()` — which runs on the first line of every message — and
        break every incoming message for anyone holding a saved workflow.
        """
        try:
            action = MenuAction(record["action"])
        except (KeyError, ValueError):
            return None
        return cls(action=action, label=record.get("label", ""), payload=record.get("payload"))


# A choice followed by the instruction it needs: "6 dont want to pay over 10 bucks".
# Without this the whole sentence fell through to Amazon as a search query and came
# back with "A Smell of Honey" — a real listing, and nothing the user asked for.
NUMERIC_WITH_ARGUMENT = re.compile(r"^\s*[#(]?\s*(\d{1,2})\s*[).:,\-–]?\s+(\S.*)$", re.DOTALL)
# Several choices at once: "1,2" or "1 and 3". Used for removing more than one item.
MULTI_CHOICE = re.compile(r"^\s*\d{1,2}(?:\s*(?:,|and|&|\+)\s*\d{1,2})+\s*[.!]?\s*$", re.IGNORECASE)


def choose(message: str, options: list[MenuOption]) -> MenuOption | None:
    """Return the option the user picked, or None if this was not a menu choice."""
    if not options:
        return None
    match = NUMERIC_REPLY.match(message or "")
    if not match:
        return None
    index = int(match.group(1))
    return options[index - 1] if 1 <= index <= len(options) else None


def choose_with_argument(
    message: str, options: list[MenuOption]
) -> tuple[MenuOption, str] | None:
    """Read a choice that carries its own instruction, or None.

    Only a number that names a real option counts, so an ordinary sentence starting
    with a digit is not mistaken for a menu pick.
    """
    if not options:
        return None
    match = NUMERIC_WITH_ARGUMENT.match(message or "")
    if not match:
        return None
    index = int(match.group(1))
    if not 1 <= index <= len(options):
        return None
    return options[index - 1], match.group(2).strip()


def choose_many(message: str, options: list[MenuOption]) -> list[MenuOption] | None:
    """Read "1,2" or "1 and 3" as several picks, or None when it is not that."""
    if not options or not MULTI_CHOICE.match(message or ""):
        return None
    numbers = [int(value) for value in re.findall(r"\d{1,2}", message)]
    if any(not 1 <= number <= len(options) for number in numbers):
        return None
    # Order preserved, duplicates dropped, so "2,2,1" removes two distinct items.
    seen: list[MenuOption] = []
    for number in numbers:
        option = options[number - 1]
        if option not in seen:
            seen.append(option)
    return seen or None


def render(options: list[MenuOption], *, start: int = 1, heading: str | None = None) -> str:
    """Render the menu as the numbered lines the user replies to.

    Labels carry product titles, which are attacker-controlled text, so they are escaped
    here: an unescaped "&" or "<" in a title would break the whole Telegram message.
    """
    if not options:
        return ""
    lines = [f"<b>{escape(heading, quote=False)}</b>"] if heading else []
    lines.extend(
        f"{number} · {escape(option.label, quote=False)}"
        for number, option in enumerate(options, start=start)
    )
    return "\n".join(lines)


def out_of_range_hint(message: str, options: list[MenuOption]) -> str | None:
    """Explain a number that is not on the menu, rather than guessing at it."""
    match = NUMERIC_REPLY.match(message or "")
    if not match or not options:
        return None
    if 1 <= int(match.group(1)) <= len(options):
        return None
    return f"There's no option {int(match.group(1))}. Choose 1 to {len(options)}."
