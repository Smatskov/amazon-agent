# Coordinates the AI agent by deciding what actions to take and delegating work to other modules.

from llm_client import generate_response


async def agent_brain(message: str) -> str:
    """Coordinate a complete model response without exposing LM Studio to Telegram."""
    try:
        return await generate_response(message)
    except Exception as error:
        print(f"LM Studio error: {error}")
        return (
            "I couldn't reach the local AI model. "
            "Make sure LM Studio and its server are running."
        )
