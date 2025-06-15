import json
import os
import logging
import asyncio
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from fastapi import FastAPI, Request

# Enable logging
logging.basicConfig(level=logging.INFO)

# Firebase setup
cred = credentials.Certificate("firebase_key.json")
try:
    firebase_admin.get_app()
except ValueError:
    firebase_admin.initialize_app(cred, {
        "databaseURL": os.getenv("FIREBASE_DB_URL")
    })

ref = db.reference("movies")

def get_movies():
    return ref.get() or {}

def save_movies(data):
    ref.set(data)

# Telegram and FastAPI setup
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6301044201"))

app = ApplicationBuilder().token(BOT_TOKEN).build()
fastapi_app = FastAPI()

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    movies = get_movies()
    if not movies:
        await update.message.reply_text("🎬 No movies available right now.")
        return

    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"movie|{title}")]
        for title in movies.keys()
    ]
    await update.message.reply_text("🎬 Choose a movie to download:", reply_markup=InlineKeyboardMarkup(keyboard))

# /search command
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    movies = get_movies()
    if not context.args:
        await update.message.reply_text("❗️ Please provide a search query.")
        return

    query = ' '.join(context.args).lower()
    results = {title: link for title, link in movies.items() if query in title.lower()}

    if not results:
        suggestions = [title for title in movies if any(q in title.lower() for q in query.split())]
        if suggestions:
            suggestion_text = "\n".join(f"🔸 {s}" for s in suggestions)
            await update.message.reply_text(f"❌ No exact matches, but you might like:\n{suggestion_text}")
        else:
            await update.message.reply_text("🔍 No matching movies found.")
        return

    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"movie|{title}")]
        for title in results
    ]
    await update.message.reply_text(f"🔍 Search results for '{query}':", reply_markup=InlineKeyboardMarkup(keyboard))

# Handle button clicks
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception as e:
        logging.warning("❌ Failed to answer callback query: %s", e)

    data = query.data
    movies = get_movies()

    if data.startswith("movie|"):
        title = data.split("|")[1]
        movie_data = movies.get(title)

        if isinstance(movie_data, dict):
            keyboard = [
                [InlineKeyboardButton(q, callback_data=f"quality|{title}|{q}")]
                for q in movie_data
            ]
            await query.message.reply_text(
                f"🎥 Choose quality for *{title}*:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.message.reply_text(
                f"🎬 {title}\n📥 [Download here]({movie_data})",
                parse_mode="Markdown"
            )

    elif data.startswith("quality|"):
        _, title, quality = data.split("|")
        link = movies.get(title, {}).get(quality)
        if link:
            await query.message.reply_text(
                f"🎬 {title} ({quality})\n📥 [Download here]({link})",
                parse_mode='Markdown'
            )
        else:
            await query.message.reply_text("❌ Link not found for this quality.")

# /addmovie command
async def add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to add movies.")
        return

    try:
        args = context.args
        if len(args) < 3:
            await update.message.reply_text("⚠️ Usage:\n/addmovie Title Quality Link", parse_mode="Markdown")
            return

        title = args[0]
        quality = args[1]
        link = args[2]

        movies = get_movies()
        if title in movies:
            if isinstance(movies[title], dict):
                movies[title][quality] = link
            else:
                movies[title] = {quality: link}
        else:
            movies[title] = {quality: link}

        save_movies(movies)
        await update.message.reply_text(f"✅ Movie *{title}* ({quality}) added successfully!", parse_mode="Markdown")
    except Exception as e:
        logging.error("❌ Error adding movie: %s", e)
        await update.message.reply_text("❌ Failed to add movie.")

# /removemovie command
async def remove_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to remove movies.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Usage:\n/removemovie Title", parse_mode="Markdown")
        return

    title = ' '.join(context.args)
    movies = get_movies()

    if title in movies:
        del movies[title]
        save_movies(movies)
        await update.message.reply_text(f"🗑️ Movie *{title}* removed successfully!", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Movie not found.")

# Register handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("addmovie", add_movie))
app.add_handler(CommandHandler("removemovie", remove_movie))
app.add_handler(CallbackQueryHandler(button))

# FastAPI webhook
@fastapi_app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        logging.info("📥 Telegram update received.")
        update = Update.de_json(data, app.bot)
        await app.initialize()
        await app.process_update(update)
        return {"ok": True}
    except Exception as e:
        logging.error("❌ Error in webhook: %s", e)
        return {"ok": False}

# ✅ FastAPI root route to test deployment
@fastapi_app.get("/")
async def root():
    return {"status": "Bot is running"}

# Webhook setup
@fastapi_app.on_event("startup")
async def on_startup():
    try:
        webhook_url = "https://movies-bot-1-uukn.onrender.com/webhook"
        logging.info(f"🌐 Setting webhook: {webhook_url}")
        await app.bot.set_webhook(webhook_url)
        logging.info("✅ Webhook set successfully")
    except Exception as e:
        logging.error("❌ Webhook setup failed: %s", e)

# Uvicorn launch
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:fastapi_app", host="0.0.0.0", port=port)
