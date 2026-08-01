# This module is the only place that communicates with the language model.
# The rest of the application should never talk directly to LM Studio.

import os
import json
import logging

from dotenv import load_dotenv
from openai import AsyncOpenAI


logger = logging.getLogger(__name__)

# Load configuration from the .env file.
load_dotenv()

# Read the model configuration from environment variables instead of hardcoding.
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL")

client = AsyncOpenAI(
    base_url=LLM_BASE_URL,
    api_key="lm-studio",
    timeout=125.0,
)


async def generate_response(
    message: str,
    *,
    max_tokens: int | None = None,
    temperature: float = 0.3,
    json_mode: bool = False,
    system_prompt: str | None = None,
) -> str:
    """Return the visible, completed response from the local language model."""
    # This module owns the OpenAI-compatible protocol. The rest of the application
    # receives plain text and does not need to know LM Studio's response format.
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": message})
    request = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        request["max_tokens"] = max_tokens
    response = await client.chat.completions.create(**request)

    choice_message = response.choices[0].message
    content = _structured_content(
        choice_message.content,
        getattr(choice_message, "reasoning_content", None),
        json_mode=json_mode,
        mode="plain_json_prompt" if json_mode else "text",
        model=getattr(response, "model", None),
        finish_reason=getattr(response.choices[0], "finish_reason", None),
    )


def _structured_content(
    content: str | None,
    reasoning_content: str | None,
    *,
    json_mode: bool,
    mode: str,
    model: str | None,
    finish_reason: str | None,
) -> str:
    """Prefer visible output; permit only a complete JSON object from LM Studio reasoning."""
    visible = content.strip() if isinstance(content, str) else ""
    reasoning = reasoning_content.strip() if isinstance(reasoning_content, str) else ""
    logger.debug(
        "LM Studio response shape model=%s mode=%s content_present=%s content_length=%s "
        "reasoning_present=%s reasoning_length=%s finish_reason=%s",
        model, mode, bool(visible), len(visible), bool(reasoning), len(reasoning), finish_reason,
    )
    if visible:
        return visible
    if not json_mode or not reasoning:
        return ""
    try:
        parsed = json.loads(reasoning)
    except json.JSONDecodeError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    # json.loads rejects prefixes, markdown, incomplete objects, and multiple objects.
    return json.dumps(parsed, separators=(",", ":"))
