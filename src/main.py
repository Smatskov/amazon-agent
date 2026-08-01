# Entry point for the application. Receives Telegram messages and sends replies.
from dotenv import load_dotenv
import os
import re
from telegram import InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from agent import agent_brain, take_pending_photos

TAG = re.compile(r"<[^>]+>")

load_dotenv()

TELEGRAM_MESSAGE_LIMIT = 4096


def _authorized_user_id() -> int:
    """Return 0 when unset or unparseable so authorization fails closed."""
    value = os.getenv("AUTHORIZED_TELEGRAM_USER_ID", "").strip()
    return int(value) if value.isdigit() else 0


# Read at import so the module stays importable without a .env; startup validates.
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AUTHORIZED_USER_ID = _authorized_user_id()


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
    # Log only routing metadata. Message text, names, and usernames are user content
    # and are deliberately kept out of the terminal log of a purchasing bot.
    print(f"[TELEGRAM] authorized message user_id={update.effective_user.id} chars={len(update.message.text or '')}")

    # Telegram behavior stays in this module. The placeholder gives feedback while
    # agent.py waits for the complete response from the local model.
    reply = await update.message.reply_text("Thinking…")
    response = await agent_brain(update.message.text, telegram_user_id=update.effective_user.id)

    if not response or not response.strip():
        response = "The agent returned an empty response."

    sections = _telegram_sections(response)
    await _send(reply.edit_text, sections[0])

    # Extra completed sections are sent only after the model has finished, so
    # long responses are preserved without reintroducing incremental streaming.
    for section in sections[1:]:
        await _send(update.message.reply_text, section)

    await _send_photos(update, take_pending_photos(update.effective_user.id))


TELEGRAM_ALBUM_LIMIT = 10
CAPTION_LIMIT = 1024


async def _send_photos(update: Update, photos: list[list[str]]) -> None:
    """Send the product images for the results just listed.

    Pictures are the only way to judge size and pack from a chat message, so they are
    worth an extra send. They are strictly an enhancement: any failure is logged and
    swallowed, because a broken image URL must never cost the user their results.
    """
    if not photos:
        return
    try:
        media = [
            InputMediaPhoto(media=url, caption=caption[:CAPTION_LIMIT])
            for url, caption in photos[:TELEGRAM_ALBUM_LIMIT]
            if url
        ]
        if len(media) == 1:
            await update.message.reply_photo(media[0].media, caption=media[0].caption)
        elif media:
            await update.message.reply_media_group(media)
    except Exception as error:  # noqa: BLE001 - images must never break a reply
        print(f"[TELEGRAM] could not send product images: {error}")


async def _send(send, text: str) -> None:
    """Send as HTML, falling back to plain text rather than losing the message.

    Replies are composed as Telegram HTML so bold renders instead of the user seeing
    literal markup. If a stray tag ever makes Telegram reject the message, the content
    still has to arrive, so it is retried with the tags stripped.
    """
    try:
        await send(text, parse_mode=ParseMode.HTML)
    except BadRequest as error:
        print(f"[TELEGRAM] HTML rejected ({error}); sending as plain text")
        await send(TAG.sub("", text))


def main():
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", BOT_TOKEN),
            ("AUTHORIZED_TELEGRAM_USER_ID", AUTHORIZED_USER_ID),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing required configuration: {', '.join(missing)}. "
            "Copy .env.example to .env and fill it in."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    print("Amazon Agent is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
