# Entry point for the application. Receives Telegram messages and sends replies.
from dotenv import load_dotenv
import os
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from agent import agent_brain

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AUTHORIZED_USER_ID = int(os.getenv("AUTHORIZED_TELEGRAM_USER_ID"))
TELEGRAM_MESSAGE_LIMIT = 4096


def _telegram_sections(text: str) -> list[str]:
    """Split a completed response so no Telegram message exceeds its text limit."""
    return [
        text[start : start + TELEGRAM_MESSAGE_LIMIT]
        for start in range(0, len(text), TELEGRAM_MESSAGE_LIMIT)
    ]


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        print("Unauthorized user attempted to access the bot.")
        return
    print("\n==============================")
    print("Telegram User ID:", update.effective_user.id)
    print("Name:", update.effective_user.full_name)
    print("Username:", update.effective_user.username)
    print("Message:", update.message.text)
    print("==============================\n")

    # Telegram behavior stays in this module. The placeholder gives feedback while
    # agent.py waits for the complete response from the local model.
    reply = await update.message.reply_text("Thinking…")
    response = await agent_brain(update.message.text)

    if not response or not response.strip():
        response = "The agent returned an empty response."

    sections = _telegram_sections(response)
    await reply.edit_text(sections[0])

    # Extra completed sections are sent only after the model has finished, so
    # long responses are preserved without reintroducing incremental streaming.
    for section in sections[1:]:
        await update.message.reply_text(section)
   


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    print("Amazon Agent is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
