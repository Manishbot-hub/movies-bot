import os
import json
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

# ----------------- 🔐 Load Firebase credentials from environment -----------------
firebase_json = os.getenv("FIREBASE_CREDENTIALS")
firebase_url = os.getenv("FIREBASE_DB_URL")

if not firebase_json or not firebase_url:
    raise ValueError("Firebase credentials or database URL is missing!")

cred = credentials.Certificate(json.loads(firebase_json))
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred, {"databaseURL": firebase_url})


# Firebase DB reference
ref = db.reference("movies")

# ----------------- 🔑 Bot Configuration -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6301044201"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set!")

app = ApplicationBuilder().token(BOT_TOKEN).build()
fastapi_app = FastAPI()

# ----------------- 📦 Firebase Helper -----------------
def get_movies():
    return ref.get() or {}

def save_movie(title, quality, link):
    ref.child(title).update({quality: link})

def delete_movie(title):
    ref.child(title).delete()

# ----------------- 🤖 Bot Commands -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to *Movies World Bot*!\n\n"
        "You can:\n"
        "• Search movies using /search <movie name>\n"
        "• Click buttons to download movies.\n",
        parse_mode="Markdown"
    )

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    movies = get_movies()
    if not context.args:
        await update.message.reply_text("❗️ Please provide a movie name. Example: `/search avengers`", parse_mode="Markdown")
        return

    query = ' '.join(context.args).lower()
    results = {title: data for title, data in movies.items() if query in title.lower()}

    if not results:
        suggestions = [title for title in movies if any(q in title.lower() for q in query.split())]
        if suggestions:
            await update.message.reply_text("No exact match found. Did you mean:\n" + '\n'.join(suggestions))
        else:
            await update.message.reply_text("❌ No matching movies found.")
        return

    keyboard = [[InlineKeyboardButton(title, callback_data=f"movie|{title}")] for title in results]
    await update.message.reply_text("🔍 Results:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    movies = get_movies()

    try:
        if data.startswith("movie|"):
            title = data.split("|")[1]
            movie_data = movies.get(title)

            if isinstance(movie_data, dict):
                keyboard = [[InlineKeyboardButton(q, callback_data=f"quality|{title}|{q}")] for q in movie_data]
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
                    parse_mode="Markdown"
                )
            else:
                await query.message.reply_text("❌ Link not found.")
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
            await update.message.reply_text("⚠️ Usage:\n`/addmovie Title Quality Link`", parse_mode="Markdown")
            return

        # Combine all parts except last two into the title
        *title_parts, quality, link = args
        title = "_".join(title_parts)

        # Firebase logic
        movies = get_movies()
        if title in movies:
            if isinstance(movies[title], dict):
                movies[title][quality] = link
            else:
                movies[title] = {quality: link}
        else:
            movies[title] = {quality: link}

        ref.set(movies)
        await update.message.reply_text(f"✅ Movie *{title}* ({quality}) added!", parse_mode="Markdown")
    except Exception as e:
        print("❌ Error adding movie:", e)
        await update.message.reply_text("❌ Failed to add movie.")




async def remove_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to remove movies.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/removemovie Title`", parse_mode="Markdown")
        return

    title = ' '.join(context.args)
    delete_movie(title)
    await update.message.reply_text(f"🗑️ Movie *{title}* removed.", parse_mode="Markdown")

# ----------------- 🧠 Handlers -----------------
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("addmovie", add_movie))
app.add_handler(CommandHandler("removemovie", remove_movie))
app.add_handler(CallbackQueryHandler(button))

# ----------------- 🌐 FastAPI Routes -----------------
@fastapi_app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.initialize()
    await app.process_update(update)
    return {"ok": True}

@fastapi_app.get("/")
async def root():
    return {"status": "Bot is running"}

@fastapi_app.on_event("startup")
async def on_startup():
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        raise ValueError("WEBHOOK_URL is not set.")
    await app.bot.set_webhook(webhook_url)

# ----------------- ▶️ Start Server -----------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:fastapi_app", host="0.0.0.0", port=port)
