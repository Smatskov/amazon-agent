# This module is the only place that communicates with the language model.
# The rest of the application should never talk directly to LM Studio.

import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

# Load configuration from the .env file.
load_dotenv()

# Read the model configuration from environment variables instead of hardcoding.
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL")

client = AsyncOpenAI(
    base_url=LLM_BASE_URL,
    api_key="lm-studio",
    timeout=60.0,
)


async def generate_response(message: str) -> str:
    """Return the visible, completed response from the local language model."""
    # This module owns the OpenAI-compatible protocol. The rest of the application
    # receives plain text and does not need to know LM Studio's response format.
    response = await client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {
                "role": "user",
                "content": message,
            }
        ],
        temperature=0.3,
    )

    # `content` is the user-visible answer. Any model reasoning fields are not
    # included in the Telegram reply.
    content = response.choices[0].message.content

    if not content or not content.strip():
        raise ValueError("The local model returned an empty response.")

    return content.strip()
