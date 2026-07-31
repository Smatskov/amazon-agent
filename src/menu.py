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
    def from_record(cls, record: dict[str, Any]) -> "MenuOption":
        return cls(
            action=MenuAction(record["action"]),
            label=record["label"],
            payload=record.get("payload"),
        )


def choose(message: str, options: list[MenuOption]) -> MenuOption | None:
    """Return the option the user picked, or None if this was not a menu choice."""
    if not options:
        return None
    match = NUMERIC_REPLY.match(message or "")
    if not match:
        return None
    index = int(match.group(1))
    return options[index - 1] if 1 <= index <= len(options) else None


def render(options: list[MenuOption], *, start: int = 1, heading: str | None = None) -> str:
    """Render the menu as the numbered lines the user replies to."""
    if not options:
        return ""
    lines = [f"<b>{heading}</b>"] if heading else []
    lines.extend(
        f"{number} · {option.label}"
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
