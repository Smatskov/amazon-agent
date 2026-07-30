"""Curated routing examples the agent reads to decide what a message means.

Deliberately read-only. The agent cannot tell reliably whether its own past turn was
correct, so letting it append would compound its mistakes into future prompts. New
examples are added by a person editing the file; nothing here ever writes to it.

The file is line-delimited JSON so a single malformed line can be skipped instead of
breaking the whole corpus.
"""

import json
from pathlib import Path

DEFAULT_EXAMPLES_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "examples" / "routing_examples.jsonl"
)
MAX_PROMPT_EXAMPLES = 8
REQUIRED_FIELDS = {"message", "route", "action"}

_cache: dict[Path, tuple[dict, ...]] = {}


def load(path: str | Path = DEFAULT_EXAMPLES_PATH) -> tuple[dict, ...]:
    """Return the curated examples, cached and tolerant of a bad line."""
    resolved = Path(path)
    if resolved in _cache:
        return _cache[resolved]

    examples: list[dict] = []
    try:
        for number, line in enumerate(resolved.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                print(f"[EXAMPLES] skipped malformed line {number} in {resolved.name}")
                continue
            if isinstance(record, dict) and REQUIRED_FIELDS <= set(record):
                examples.append(record)
    except OSError:
        # A missing corpus must never break message handling.
        print(f"[EXAMPLES] no corpus at {resolved}; continuing without examples")

    _cache[resolved] = tuple(examples)
    return _cache[resolved]


def reset_cache() -> None:
    _cache.clear()


def _overlap(message: str, example: str) -> int:
    left = {word for word in message.casefold().split() if len(word) > 2}
    right = {word for word in example.casefold().split() if len(word) > 2}
    return len(left & right)


def similar(message: str, *, limit: int = MAX_PROMPT_EXAMPLES, path=DEFAULT_EXAMPLES_PATH) -> list[dict]:
    """Return the examples most likely to help interpret this message.

    Word overlap is crude but predictable, and it keeps the corpus useful without a
    second model call in the hot path.
    """
    scored = [(_overlap(message, record["message"]), record) for record in load(path)]
    ranked = sorted(scored, key=lambda row: -row[0])
    return [record for score, record in ranked[:limit] if score > 0]


def prompt_block(message: str, *, limit: int = MAX_PROMPT_EXAMPLES, path=DEFAULT_EXAMPLES_PATH) -> str:
    """Render matching examples for inclusion in a semantic prompt."""
    matches = similar(message, limit=limit, path=path)
    if not matches:
        return ""
    lines = [
        f'"{record["message"]}" -> route={record["route"]} action={record["action"]}'
        for record in matches
    ]
    return "Examples of correct interpretation:\n" + "\n".join(lines)
