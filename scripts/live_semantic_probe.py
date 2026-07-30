"""Read-only focused probe of the production semantic client contract."""

import asyncio
import argparse
import json
from time import perf_counter

import intent_classifier
from timing import RequestTiming


MESSAGES = [
    "What was my favorite toothpaste?",
    "Remember that my favorite drink is Coke.",
    "Buy toothpaste.",
    "What is the capital of France?",
]


async def main(limit: int | None) -> None:
    for message in MESSAGES[:limit]:
        timing = RequestTiming.start()
        timing.mark_prepare_complete()
        started = perf_counter()
        action = await intent_classifier.interpret_message(message, timing=timing)
        print(json.dumps({
            "message_kind": "memory_recall" if "favorite toothpaste" in message else "memory_remember" if "favorite drink" in message else "purchase" if message.startswith("Buy") else "general_chat",
            "route": action.route,
            "action": action.action,
            "classification_valid": action.classification_valid,
            "would_execute": action.route in {"memory", "purchase", "workflow"} and action.action != "no_match",
            "total_ms": round((perf_counter() - started) * 1000),
        }, sort_keys=True))
        timing.log()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    asyncio.run(main(args.limit))
