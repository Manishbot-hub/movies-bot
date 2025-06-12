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

# Load movies
with open("movies.json", "r") as f:
    MOVIES = json.load(f)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set")

# Telegram bot app
app = ApplicationBuilder().token(BOT_TOKEN).build()

# FastAPI app
fastapi_app = FastAPI()

# Telegram commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"movie|{title}")]
        for title in MOVIES.keys()
    ]
    await update.message.reply_text(
        "🎬 Choose a movie to download:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗️ Please provide a search query.")
        return

    query = ' '.join(context.args).lower()
    results = {title: links for title, links in MOVIES.items() if query in title.lower()}

    if not results:
        await update.message.reply_text("🔍 No matching movies found.")
        return

    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"movie|{title}")]
        for title in results
    ]
    await update.message.reply_text(
        f"🔍 Search results for '{query}':",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    try:
        if data.startswith("movie|"):
            movie_title = data.split("|")[1]
            movie_data = MOVIES.get(movie_title)

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
                    parse_mode='Markdown'
                )

        elif data.startswith("quality|"):
            _, movie_title, quality = data.split("|")
            link = MOVIES.get(movie_title, {}).get(quality)
            if link:
                await query.message.reply_text(
                    f"🎬 {movie_title} ({quality})\n📥 [Download here]({link})",
                    parse_mode='Markdown'
                )
            else:
                await query.message.reply_text("❌ Link not found for this quality.")
    except Exception as e:
        print("❌ Error in button handler:", e)

# Register handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CallbackQueryHandler(button))

# ✅ Webhook endpoint (ONLY ONE)
@fastapi_app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        print("📥 Raw data from Telegram:", data)  # ✅ Add this line
        update = Update.de_json(data, app.bot)
        await app.process_update(update)
        print("✅ Webhook received and processed.")
        return {"ok": True}
    except Exception as e:
        print("❌ Error processing update:", e)  # ✅ This line shows the crash
        return {"ok": False}


# Set webhook on startup
@fastapi_app.on_event("startup")
async def on_startup():
    try:
        webhook_url = "https://movies-bot-ydtm.onrender.com/webhook"
        print(f"🌐 Setting webhook to: {webhook_url}")
        await app.bot.set_webhook(webhook_url)
        print("✅ Webhook set successfully")
    except Exception as e:
        print("❌ Error in on_startup:", e)

# Run with Uvicorn
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:fastapi_app", host="0.0.0.0", port=port)
