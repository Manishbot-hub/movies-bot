
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

ADMIN_ID = 6301044201

MOVIES_FILE = "movies.json"
if os.path.exists(MOVIES_FILE):
    with open(MOVIES_FILE, "r") as f:
        MOVIES = json.load(f)
else:
    MOVIES = {}

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set")

app = ApplicationBuilder().token(BOT_TOKEN).build()
fastapi_app = FastAPI()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "👋 Welcome to *Movies World*!

"
        "🎬 You can:
"
        "🔍 Search a movie with `/search MovieName`

"
        "Type a command to begin!"
    )
    await update.message.reply_text(message, parse_mode="Markdown")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗️ Please provide a search query.")
        return

    query = ' '.join(context.args).lower()
    results = {title: links for title, links in MOVIES.items() if query in title.lower()}

    if not results:
        suggestions = [title for title in MOVIES if any(q in title.lower() for q in query.split())]
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

async def add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
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

        if title in MOVIES:
            if isinstance(MOVIES[title], dict):
                MOVIES[title][quality] = link
            else:
                MOVIES[title] = {quality: link}
        else:
            MOVIES[title] = {quality: link}

        with open(MOVIES_FILE, "w") as f:
            json.dump(MOVIES, f, indent=2)

        await update.message.reply_text(f"✅ Movie *{title}* ({quality}) added successfully!", parse_mode="Markdown")
    except Exception as e:
        print("❌ Error adding movie:", e)
        await update.message.reply_text("❌ Failed to add movie.")

async def remove_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to remove movies.")
        return

    try:
        if not context.args:
            await update.message.reply_text("⚠️ Usage:\n/removemovie MovieName", parse_mode="Markdown")
            return

        title = ' '.join(context.args)
        if title in MOVIES:
            del MOVIES[title]
            with open(MOVIES_FILE, "w") as f:
                json.dump(MOVIES, f, indent=2)
            await update.message.reply_text(f"🗑️ Movie *{title}* removed successfully!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Movie not found.")
    except Exception as e:
        print("❌ Error removing movie:", e)
        await update.message.reply_text("❌ Failed to remove movie.")

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("addmovie", add_movie))
app.add_handler(CommandHandler("removemovie", remove_movie))
app.add_handler(CallbackQueryHandler(button))

@fastapi_app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        print("📥 Telegram data:", data)
        update = Update.de_json(data, app.bot)
        await app.initialize()
        await app.process_update(update)
        print("✅ Webhook processed.")
        return {"ok": True}
    except Exception as e:
        print("❌ Error processing update:", e)
        return {"ok": False}

@fastapi_app.on_event("startup")
async def on_startup():
    try:
        webhook_url = "https://movies-bot-1-uukn.onrender.com/webhook"
        print(f"🌐 Setting webhook to: {webhook_url}")
        await app.bot.set_webhook(webhook_url)
        print("✅ Webhook set successfully")
    except Exception as e:
        print("❌ Error in on_startup:", e)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:fastapi_app", host="0.0.0.0", port=port)
