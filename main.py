import json
import os
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from fastapi import FastAPI, Request
import telegram

# Load movies
with open("movies.json", "r") as f:
    MOVIES = json.load(f)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set")

# Telegram bot app
app = ApplicationBuilder().token(BOT_TOKEN).build()

# FastAPI app for webhook
fastapi_app = FastAPI()


# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"movie|{title}")]
        for title in MOVIES.keys()
    ]
    await update.message.reply_text("🎬 Choose a movie to download:", reply_markup=InlineKeyboardMarkup(keyboard))


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗️ Please provide a search query. Example: /search inception")
        return

    query = ' '.join(context.args).lower()
    results = {title: links for title, links in MOVIES.items() if query in title.lower()}

    if not results:
        await update.message.reply_text("🔍 No movies found matching your query.")
        return

    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"movie|{title}")]
        for title in results.keys()
    ]
    await update.message.reply_text(f"🔍 Search results for '{query}':", reply_markup=InlineKeyboardMarkup(keyboard))


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("movie|"):
        movie_title = data.split("|")[1]
        movie_data = MOVIES.get(movie_title)

        if isinstance(movie_data, dict):
            keyboard = [
                [InlineKeyboardButton(q, callback_data=f"quality|{movie_title}|{q}")]
                for q in movie_data.keys()
            ]
            await query.message.reply_text(
                f"🎥 Choose quality for *{movie_title}*:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.message.reply_text(
                f"🎬 {movie_title}\n📥 [Download here]({movie_data})",
                parse_mode="Markdown"
            )

    elif data.startswith("quality|"):
        _, movie_title, quality = data.split("|")
        movie_links = MOVIES.get(movie_title, {})
        link = movie_links.get(quality)

        if link:
            await query.message.reply_text(
                f"🎬 {movie_title} ({quality})\n📥 [Download here]({link})",
                parse_mode='Markdown'
            )
        else:
            await query.message.reply_text("❌ Link not found for this quality.")


# Add handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CallbackQueryHandler(button))


# FastAPI route to handle webhook
@fastapi_app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return {"ok": True}


# Set the webhook when the bot starts
async def set_webhook():
   url = "https://movies-bot-ydtm.onrender.com/webhook"
  # Replace with your actual Render domain
    await app.bot.set_webhook(url)


# Start everything
if __name__ == "__main__":
    async def main():
        await app.initialize()
        await app.start()
        await set_webhook()
        await asyncio.Event().wait()

    asyncio.run(main())
