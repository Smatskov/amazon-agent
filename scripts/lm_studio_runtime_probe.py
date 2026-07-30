"""Compare raw HTTP and OpenAI SDK response shapes without exposing reasoning text."""

import asyncio
import argparse
import json
from time import perf_counter
from urllib.parse import urljoin

import httpx

from llm_client import LLM_BASE_URL, LLM_MODEL, client


PROMPT = 'Return exactly this JSON object and nothing else: {"status":"ok"}'


def summarize(payload: dict, *, mode: str, transport: str, elapsed_ms: int, status: int) -> dict:
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    reasoning = message.get("reasoning_content") or ""
    try:
        json_ok = isinstance(json.loads(content), dict)
    except (TypeError, json.JSONDecodeError):
        json_ok = False
    return {
        "mode": mode, "transport": transport, "http_status": status, "elapsed_ms": elapsed_ms,
        "top_level_keys": sorted(payload), "model": payload.get("model"),
        "finish_reason": choice.get("finish_reason"), "content_present": bool(content),
        "content_length": len(content), "reasoning_present": bool(reasoning),
        "reasoning_length": len(reasoning), "visible_json_ok": json_ok,
        "usage": payload.get("usage"),
    }


async def raw(mode: str, body: dict) -> dict:
    started = perf_counter()
    async with httpx.AsyncClient(timeout=40) as http:
        response = await http.post(urljoin(LLM_BASE_URL.rstrip("/") + "/", "chat/completions"), json=body)
    elapsed = round((perf_counter() - started) * 1000)
    return summarize(response.json(), mode=mode, transport="raw_http", elapsed_ms=elapsed, status=response.status_code)


async def sdk(mode: str, body: dict) -> dict:
    started = perf_counter()
    response = await client.chat.completions.create(**body)
    elapsed = round((perf_counter() - started) * 1000)
    return summarize(response.model_dump(), mode=mode, transport="openai_sdk", elapsed_ms=elapsed, status=200)


async def main(selected_mode: str | None) -> None:
    base = {"model": LLM_MODEL, "messages": [{"role": "user", "content": PROMPT}], "temperature": 0, "max_tokens": 128}
    modes = {
        "plain": base,
        "template_thinking_disabled": {**base, "chat_template_kwargs": {"enable_thinking": False}},
        "json_object": {**base, "response_format": {"type": "json_object"}},
    }
    for mode, body in modes.items():
        if selected_mode and mode != selected_mode:
            continue
        for runner in (raw, sdk):
            try:
                print(json.dumps(await runner(mode, body), sort_keys=True))
            except Exception as error:
                print(json.dumps({"mode": mode, "transport": runner.__name__, "error": repr(error)}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("plain", "template_thinking_disabled", "json_object"))
    args = parser.parse_args()
    asyncio.run(main(args.mode))
