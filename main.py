import json
 
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
import os

# Load movie list with qualities
with open("movies.json", "r") as file:
    MOVIES = json.load(file)

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

# Load movies
with open("movies.json", "r") as f:
    MOVIES = json.load(f)
 b3fd2864a41c3e5ea533293e5d4f866f25daf6f8

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set")

 
# /start command — show movie list

# Telegram bot application
app = ApplicationBuilder().token(BOT_TOKEN).build()

# FastAPI app
fastapi_app = FastAPI()


# Handlers
 b3fd2864a41c3e5ea533293e5d4f866f25daf6f8
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"movie|{title}")]
        for title in MOVIES.keys()
    ]
 
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🎬 Choose a movie to download:", reply_markup=reply_markup)

# /search command — search movie titles
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗️ Please provide a search query. Example: /search inception")
        return

    query = ' '.join(context.args).lower()
    results = {title: links for title, links in MOVIES.items() if query in title.lower()}

    if not results:
        await update.message.reply_text("🔍 No movies found matching your query.")

    await update.message.reply_text(
        "🎬 Choose a movie to download:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗️ Please provide a search query.")
        return

    query = ' '.join(context.args).lower()
    results = {t: links for t, links in MOVIES.items() if query in t.lower()}
    if not results:
        await update.message.reply_text("🔍 No matching movies found.")
 b3fd2864a41c3e5ea533293e5d4f866f25daf6f8
        return

    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"movie|{title}")]
 
        for title in results.keys()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"🔍 Search results for '{query}':", reply_markup=reply_markup)

# Handle button taps

        for title in results
    ]
    await update.message.reply_text(
        f"🔍 Search results for '{query}':",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


 b3fd2864a41c3e5ea533293e5d4f866f25daf6f8
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
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text(f"🎥 Choose quality for *{movie_title}*:", parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await query.message.reply_text(
                f"🎬 {movie_title}
📥 [Download here]({movie_data})",
                parse_mode='Markdown'

        if isinstance(movie_data, dict):
            keyboard = [
                [InlineKeyboardButton(q, callback_data=f"quality|{movie_title}|{q}")]
                for q in movie_data
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
 b3fd2864a41c3e5ea533293e5d4f866f25daf6f8
            )

    elif data.startswith("quality|"):
        _, movie_title, quality = data.split("|")
 
        movie_links = MOVIES.get(movie_title, {})
        link = movie_links.get(quality)

        if link:
            await query.message.reply_text(
                f"🎬 {movie_title} ({quality})
📥 [Download here]({link})",

        link = MOVIES.get(movie_title, {}).get(quality)
        if link:
            await query.message.reply_text(
                f"🎬 {movie_title} ({quality})\n📥 [Download here]({link})",
 b3fd2864a41c3e5ea533293e5d4f866f25daf6f8
                parse_mode='Markdown'
            )
        else:
            await query.message.reply_text("❌ Link not found for this quality.")

 
# Run bot
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CallbackQueryHandler(button))
    app.run_polling()


# Register handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CallbackQueryHandler(button))


@fastapi_app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, app.bot)
        await app.process_update(update)
        print("✅ Processed update:", data)  # ✅ Success log
        return {"ok": True}
    except Exception as e:
        print("❌ Error processing update:", e)  # ❌ Error log
        return {"ok": False}



# Set webhook when FastAPI starts
@fastapi_app.on_event("startup")
async def on_startup():
    webhook_url = "https://movies-bot-ydtm.onrender.com/webhook"
    await app.bot.set_webhook(webhook_url)


# Expose FastAPI via CLI when executed with python
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:fastapi_app", host="0.0.0.0", port=port)
 b3fd2864a41c3e5ea533293e5d4f866f25daf6f8
