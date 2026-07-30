"""Read-only LM Studio structured-output compatibility probe.

Run with: PYTHONPATH=src .venv/bin/python scripts/lm_studio_diagnostics.py
It intentionally reports response shape and lengths, never model reasoning text or
environment values.
"""

import asyncio
import argparse
from dataclasses import asdict, dataclass
import json
from time import perf_counter
from typing import Any

from llm_client import LLM_MODEL, client


MEMORY_PROMPT = (
    "Return exactly one JSON object and nothing else. No markdown. No explanation. "
    "Extract this memory request: What was my favorite toothpaste? "
    "Use exactly these fields: action, key, value, confidence. "
    "Allowed action values: remember, recall, forget, no_match. "
    "For recall use value null."
)
SYSTEM_PROMPT = "You are a JSON extractor. Return only the final JSON object, never reasoning."
MEMORY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "key", "value", "confidence"],
    "properties": {
        "action": {"type": "string", "enum": ["remember", "recall", "forget", "no_match"]},
        "key": {"type": ["string", "null"]},
        "value": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


@dataclass
class ProbeResult:
    mode: str
    http_ok: bool
    elapsed_ms: int
    first_byte_ms: int | None
    completion_ms: int | None
    model: str | None = None
    finish_reason: str | None = None
    content_present: bool = False
    content_length: int = 0
    reasoning_present: bool = False
    reasoning_length: int = 0
    reasoning_json_object: bool = False
    tool_calls_present: bool = False
    field_names: list[str] | None = None
    json_ok: bool = False
    schema_ok: bool = False
    usage: dict[str, Any] | None = None
    error: str | None = None


def _strict_json_object(content: str | None) -> dict[str, Any] | None:
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _valid_memory_contract(value: dict[str, Any] | None) -> bool:
    if not value or set(value) != {"action", "key", "value", "confidence"}:
        return False
    action = value["action"]
    key = value["key"]
    memory_value = value["value"]
    confidence = value["confidence"]
    if action not in {"remember", "recall", "forget", "no_match"}:
        return False
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return False
    if action == "recall":
        return isinstance(key, str) and bool(key.strip()) and memory_value is None
    return True


def _message_shape(message: Any) -> tuple[str | None, str | None, bool, list[str]]:
    dumped = message.model_dump() if hasattr(message, "model_dump") else vars(message)
    return (
        dumped.get("content"),
        dumped.get("reasoning_content"),
        bool(dumped.get("tool_calls")),
        sorted(dumped),
    )


async def _non_streaming(mode: str, request: dict[str, Any]) -> ProbeResult:
    started_at = perf_counter()
    try:
        response = await asyncio.wait_for(client.chat.completions.create(**request), timeout=30)
        elapsed_ms = round((perf_counter() - started_at) * 1000)
        choice = response.choices[0]
        content, reasoning, tool_calls, fields = _message_shape(choice.message)
        parsed = _strict_json_object(content)
        reasoning_json = _strict_json_object(reasoning)
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None
        return ProbeResult(
            mode, True, elapsed_ms, None, elapsed_ms,
            model=getattr(response, "model", None),
            finish_reason=choice.finish_reason,
            content_present=bool(content), content_length=len(content or ""),
            reasoning_present=bool(reasoning), reasoning_length=len(reasoning or ""),
            reasoning_json_object=reasoning_json is not None,
            tool_calls_present=tool_calls, field_names=fields,
            json_ok=parsed is not None, schema_ok=_valid_memory_contract(parsed), usage=usage,
        )
    except Exception as error:
        return ProbeResult(mode, False, round((perf_counter() - started_at) * 1000), None, None, error=repr(error))


async def _streaming(mode: str, request: dict[str, Any]) -> ProbeResult:
    started_at = perf_counter()
    first_byte_at: float | None = None
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_length = 0
    finish_reason: str | None = None
    model: str | None = None
    tool_calls_present = False
    field_names: set[str] = set()
    try:
        stream = await asyncio.wait_for(client.chat.completions.create(**request, stream=True), timeout=30)
        while True:
            try:
                chunk = await asyncio.wait_for(stream.__anext__(), timeout=30)
            except StopAsyncIteration:
                break
            if first_byte_at is None:
                first_byte_at = perf_counter()
            model = getattr(chunk, "model", model)
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta
            dumped = delta.model_dump() if hasattr(delta, "model_dump") else vars(delta)
            field_names.update(dumped)
            content = dumped.get("content")
            reasoning = dumped.get("reasoning_content")
            content_parts.append(content or "")
            reasoning_parts.append(reasoning or "")
            reasoning_length += len(reasoning or "")
            tool_calls_present = tool_calls_present or bool(dumped.get("tool_calls"))
        content = "".join(content_parts)
        parsed = _strict_json_object(content)
        reasoning_json = _strict_json_object("".join(reasoning_parts))
        elapsed_ms = round((perf_counter() - started_at) * 1000)
        return ProbeResult(
            mode, True, elapsed_ms,
            round((first_byte_at - started_at) * 1000) if first_byte_at else None,
            elapsed_ms, model=model, finish_reason=finish_reason,
            content_present=bool(content), content_length=len(content),
            reasoning_present=reasoning_length > 0, reasoning_length=reasoning_length,
            reasoning_json_object=reasoning_json is not None,
            tool_calls_present=tool_calls_present, field_names=sorted(field_names),
            json_ok=parsed is not None, schema_ok=_valid_memory_contract(parsed),
        )
    except Exception as error:
        return ProbeResult(mode, False, round((perf_counter() - started_at) * 1000), None, None, error=repr(error))


def _request(*, prompt: str = MEMORY_PROMPT, system: bool = False, max_tokens: int = 128, **extra: Any) -> dict[str, Any]:
    messages = [{"role": "user", "content": prompt}]
    if system:
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
    return {"model": LLM_MODEL, "messages": messages, "temperature": 0, "max_tokens": max_tokens, **extra}


async def main(minimal: bool, output_path: str | None) -> None:
    full_modes = [
        ("plain_user_128", _non_streaming, _request()),
        ("plain_system_user_128", _non_streaming, _request(system=True)),
        ("prompt_json_128", _non_streaming, _request(prompt=MEMORY_PROMPT + " JSON only.")),
        ("json_object_128", _non_streaming, _request(response_format={"type": "json_object"})),
        ("json_schema_128", _non_streaming, _request(response_format={"type": "json_schema", "json_schema": {"name": "memory_action", "schema": MEMORY_SCHEMA}})),
        ("plain_32", _non_streaming, _request(max_tokens=32)),
        ("plain_64", _non_streaming, _request(max_tokens=64)),
        ("plain_256", _non_streaming, _request(max_tokens=256)),
        ("json_schema_256", _non_streaming, _request(max_tokens=256, response_format={"type": "json_schema", "json_schema": {"name": "memory_action", "schema": MEMORY_SCHEMA}})),
        ("plain_reasoning_none", _non_streaming, _request(reasoning_effort="none")),
        ("json_schema_reasoning_none", _non_streaming, _request(reasoning_effort="none", response_format={"type": "json_schema", "json_schema": {"name": "memory_action", "schema": MEMORY_SCHEMA}})),
        ("plain_stream_128", _streaming, _request()),
        ("json_schema_stream_128", _streaming, _request(response_format={"type": "json_schema", "json_schema": {"name": "memory_action", "schema": MEMORY_SCHEMA}})),
    ]
    minimal_modes = [
        ("plain_system_user_128", _non_streaming, _request(system=True)),
        ("json_object_128", _non_streaming, _request(response_format={"type": "json_object"})),
        ("json_schema_128", _non_streaming, _request(response_format={"type": "json_schema", "json_schema": {"name": "memory_action", "schema": MEMORY_SCHEMA}})),
        ("plain_256", _non_streaming, _request(max_tokens=256)),
        ("plain_reasoning_none_256", _non_streaming, _request(max_tokens=256, reasoning_effort="none")),
        ("plain_stream_128", _streaming, _request()),
        ("json_schema_stream_128", _streaming, _request(response_format={"type": "json_schema", "json_schema": {"name": "memory_action", "schema": MEMORY_SCHEMA}})),
    ]
    lines: list[str] = []
    try:
        models = await client.models.list()
        lines.append(json.dumps({"models_endpoint_ok": True, "loaded_models": [model.id for model in models.data]}, sort_keys=True))
    except Exception as error:
        lines.append(json.dumps({"models_endpoint_ok": False, "error": repr(error)}, sort_keys=True))
    for mode, runner, request in (minimal_modes if minimal else full_modes):
        result = await runner(mode, request)
        lines.append(json.dumps(asdict(result), sort_keys=True))
    for line in lines:
        print(line, flush=True)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as output_file:
            output_file.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimal", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    asyncio.run(main(args.minimal, args.output))
