import os
import json
import aiohttp
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
import uvicorn

# Admin ID
ADMIN_ID = 6301044201

# Load environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
FIREBASE_URL = os.getenv("FIREBASE_URL")
ADRN_TOKEN = os.getenv("ADRIN_API_KEY")

# Firebase Init
firebase_key = json.loads(os.getenv("FIREBASE_KEY"))
cred = credentials.Certificate(firebase_key)
firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_URL})
ref = db.reference("movies")

app = ApplicationBuilder().token(BOT_TOKEN).build()
fastapi_app = FastAPI()

def get_movies():
    return ref.get() or {}

async def shorten_link(link):
    api_url = f"https://adrinolinks.com/api?api={ADRN_TOKEN}&url={link}"
    async with aiohttp.ClientSession() as session:
        async with session.get(api_url) as resp:
            data = await resp.json()
            return data.get("shortenedUrl", link)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to *Movies World Bot!*\n\n"
        "You can:\n"
        "🔎 Search with `/search movie_name`\n"
        "➕ Admins can `/addmovie`, `/uploadbulk`, `/removemovie`\n"
        "📢 Use /admin to view all admin commands.",
        parse_mode="Markdown",
    )

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗ Provide search text. Example: `/search Avengers`", parse_mode="Markdown")
        return
    query = ' '.join(context.args).lower()
    movies = get_movies()
    results = [title for title in movies if query in title.lower()]

    if not results:
        await update.message.reply_text("❌ No results found.")
        return

    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"movie|{title}")]
        for title in results
    ]
    await update.message.reply_text(
        f"🔎 Results for *{query}*:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    movies = get_movies()

    try:
        if data.startswith("movie|"):
            movie_title = data.split("|")[1]
            movie_data = movies.get(movie_title, {})
            if isinstance(movie_data, dict):
                keyboard = [
                    [InlineKeyboardButton(q, callback_data=f"quality|{movie_title}|{q}")]
                    for q in movie_data if q != "genre"
                ]
                await query.message.reply_text(
                    f"*{movie_title}*\nSelect quality:",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
        elif data.startswith("quality|"):
            _, movie_title, quality = data.split("|")
            link = movies.get(movie_title, {}).get(quality)
            if link:
                buttons = [
                    [InlineKeyboardButton("📢 Report Broken Link", callback_data=f"report|{movie_title}|{quality}")]
                ]
                await query.message.reply_text(
                    f"*{movie_title}* ({quality})\n📥 [Download Link]({link})",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
        elif data.startswith("report|"):
            _, title, quality = data.split("|")
            report_text = f"🚨 Broken Link Reported:\n*Movie*: {title}\n*Quality*: {quality}\n*User*: @{query.from_user.username} ({query.from_user.id})"
            await app.bot.send_message(chat_id=ADMIN_ID, text=report_text, parse_mode="Markdown")
            await query.message.reply_text("✅ Report submitted to Admin.")
        elif data.startswith("delete|"):
            _, title = data.split("|")
            movies.pop(title, None)
            ref.set(movies)
            await query.message.reply_text(f"🗑️ Deleted *{title}*", parse_mode="Markdown")
    except Exception as e:
        print(f"❌ Button Error: {e}")

async def add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Usage:\n/addmovie Title Quality Link", parse_mode="Markdown")
        return

    title = args[0]
    quality = args[1]
    original_link = args[2]

    link = await shorten_link(original_link)
    movies = get_movies()
    movie = movies.get(title, {})
    movie[quality] = link
    ref.child(title).set(movie)

    await update.message.reply_text(f"✅ Added *{title}* ({quality})", parse_mode="Markdown")

async def upload_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return
    if not update.message.text:
        await update.message.reply_text("❗ Paste the movie list after /uploadbulk.\nFormat:\nTitle|Quality|Link (each movie on new line)")
        return

    text = update.message.text.replace("/uploadbulk", "").strip()
    lines = text.splitlines()
    added = 0
    movies = get_movies()

    for line in lines:
        parts = line.split("|")
        if len(parts) != 3:
            continue
        title, quality, link = parts
        short_link = await shorten_link(link)
        movie = movies.get(title, {})
        movie[quality] = short_link
        ref.child(title).set(movie)
        added += 1

    await update.message.reply_text(f"✅ Bulk upload complete. Added {added} movies!")

async def remove_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage:\n/removemovie MovieName", parse_mode="Markdown")
        return

    search_term = ' '.join(context.args).lower()
    movies = get_movies()
    matches = [title for title in movies if search_term in title.lower()]

    if not matches:
        await update.message.reply_text("❌ No matching movies found.")
        return

    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"delete|{title}")]
        for title in matches
    ]
    await update.message.reply_text(
        "Select a movie to delete:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    text = (
        "👑 *Admin Commands:*\n\n"
        "/addmovie Title Quality Link\n"
        "/uploadbulk (paste multiple movies: Title|Quality|Link per line)\n"
        "/removemovie PartialTitle\n"
        "/admin (show this menu)"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, app.bot)
        await app.process_update(update)
    except Exception as e:
        print(f"❌ Webhook Error: {e}")
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "Bot is running"}

@fastapi_app.on_event("startup")
async def on_startup():
    try:
        webhook_url = os.getenv("WEBHOOK_URL")
        if not webhook_url:
            raise ValueError("WEBHOOK_URL is not set in Railway Environment Variables")
        await app.bot.set_webhook(webhook_url)
    except Exception as e:
        print(f"❌ Startup Error: {e}")

# Handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("addmovie", add_movie))
app.add_handler(CommandHandler("uploadbulk", upload_bulk))
app.add_handler(CommandHandler("removemovie", remove_movie))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(CallbackQueryHandler(button))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:fastapi_app", host="0.0.0.0", port=port)
