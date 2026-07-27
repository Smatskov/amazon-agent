import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import agent
import intent_classifier
import llm_client
import main


def _completion_with_content(content):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    reasoning_content="private reasoning is not user-visible",
                )
            )
        ]
    )


def test_generate_response_returns_visible_completed_content(monkeypatch):
    create = AsyncMock(return_value=_completion_with_content("  Local answer.  "))
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(llm_client, "client", fake_client)

    response = asyncio.run(llm_client.generate_response("Hello"))

    assert response == "Local answer."
    create.assert_awaited_once()


@pytest.mark.parametrize("content", [None, " \n\t "])
def test_generate_response_rejects_empty_visible_content(monkeypatch, content):
    create = AsyncMock(return_value=_completion_with_content(content))
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(llm_client, "client", fake_client)

    with pytest.raises(ValueError, match="empty response"):
        asyncio.run(llm_client.generate_response("Hello"))


def test_agent_brain_returns_friendly_error_when_model_call_fails(monkeypatch):
    async def failing_generate_response(message):
        raise ConnectionError("LM Studio is unavailable")

    monkeypatch.setattr(agent, "generate_response", failing_generate_response)
    monkeypatch.setattr(
        agent.intent_classifier,
        "classify_intent",
        AsyncMock(return_value=intent_classifier.IntentResult("general_chat", 0.9, {})),
    )

    response = asyncio.run(agent.agent_brain("Hello"))

    assert response == (
        "I couldn't reach the local AI model. "
        "Make sure LM Studio and its server are running."
    )


def test_telegram_sections_preserve_long_completed_response():
    response = "a" * 4096 + "b" * 4096 + "c" * 12

    sections = main._telegram_sections(response)

    assert all(len(section) <= main.TELEGRAM_MESSAGE_LIMIT for section in sections)
    assert "".join(sections) == response
    assert [len(section) for section in sections] == [4096, 4096, 12]
