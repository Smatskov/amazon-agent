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


def test_generate_response_uses_plain_json_prompt_mode(monkeypatch):
    create = AsyncMock(return_value=_completion_with_content('{"route":"general_chat","confidence":1}'))
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm_client, "client", fake_client)

    asyncio.run(llm_client.generate_response("Route this", max_tokens=32, temperature=0, json_mode=True))

    request = create.await_args.kwargs
    assert request["max_tokens"] == 32
    assert request["temperature"] == 0
    assert "response_format" not in request
    assert request["messages"] == [{"role": "user", "content": "Route this"}]


def test_generate_response_adds_user_facing_system_prompt_only_when_requested(monkeypatch):
    create = AsyncMock(return_value=_completion_with_content("Short answer."))
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm_client, "client", fake_client)

    response = asyncio.run(
        llm_client.generate_response(
            "Hello", system_prompt="User-facing policy", max_tokens=180
        )
    )

    assert response == "Short answer."
    assert create.await_args.kwargs["messages"] == [
        {"role": "system", "content": "User-facing policy"},
        {"role": "user", "content": "Hello"},
    ]


def test_visible_json_is_preferred_over_reasoning_json():
    assert llm_client._structured_content(
        '{"route":"memory","confidence":0.9}',
        '{"route":"general_chat","confidence":0.9}',
        json_mode=True, mode="test", model="test", finish_reason="stop",
    ) == '{"route":"memory","confidence":0.9}'


@pytest.mark.parametrize("reasoning", [
    '{"route":"memory","confidence":0.9}',
    'reasoning before {"route":"memory","confidence":0.9}',
    '```json\n{"route":"memory","confidence":0.9}\n```',
    '{"route":"memory"}{"confidence":0.9}',
    '{"route":"memory",',
    '[{"route":"memory"}]',
])
def test_reasoning_fallback_accepts_only_one_complete_json_object(reasoning):
    result = llm_client._structured_content(
        "", reasoning, json_mode=True, mode="test", model="test", finish_reason="stop"
    )
    if reasoning == '{"route":"memory","confidence":0.9}':
        assert result == reasoning
    else:
        assert result == ""


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
        "interpret_message",
        AsyncMock(return_value=intent_classifier.SemanticAction("general_chat", confidence=0.9)),
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
