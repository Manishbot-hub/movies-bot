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
import firebase_admin
from firebase_admin import credentials, db

# Firebase setup using env vars
firebase_key = json.loads(os.getenv("FIREBASE_CREDENTIALS", "{}"))
firebase_url = os.getenv("FIREBASE_DB_URL")
if not firebase_admin._apps:
    cred = credentials.Certificate(firebase_key)
    firebase_admin.initialize_app(cred, {"databaseURL": firebase_url})

ref = db.reference("movies")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set")

ADMIN_ID = int(os.getenv("ADMIN_ID", "6301044201"))

app = ApplicationBuilder().token(BOT_TOKEN).build()
fastapi_app = FastAPI()

def get_movies():
    return ref.get() or {}

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 Welcome to Movies World!\nUse /search to find movies ")

# /search
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗️ Provide a search keyword.")
        return

    query = ' '.join(context.args).lower()
    movies = get_movies()
    results = {k: v for k, v in movies.items() if query in k.lower()}

    if not results:
        await update.message.reply_text("❌ No matching movies.")
        return

    keyboard = [
        [InlineKeyboardButton(f"{k} ({v.get('genre', 'Unknown')})", callback_data=f"movie|{k}")]
        for k in results
    ]
    await update.message.reply_text("🔍 Results:", reply_markup=InlineKeyboardMarkup(keyboard))

# Handle buttons
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    try:
        if data.startswith("movie|"):
            title = data.split("|")[1]
            movie = get_movies().get(title)

            if not movie:
                await query.message.reply_text("❌ Movie not found.")
                return

            keyboard = [
                [InlineKeyboardButton(q, url=movie[q])] for q in movie if q not in ("genre",)
            ]
            genre = movie.get("genre", "Unknown")
            await query.message.reply_text(
                f"🎬 *{title}* ({genre})\nChoose quality:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        elif data.startswith("remove|"):
            title = data.split("|")[1]
            ref.child(title).delete()
            await query.message.reply_text(f"🗑️ Movie *{title}* removed.", parse_mode="Markdown")

    except Exception as e:
        print("❌ Button error:", e)

# /addmovie Title Genre Quality Link
async def add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    args = context.args
    if len(args) < 4:
        await update.message.reply_text("Usage:\n/addmovie Title Genre Quality Link", parse_mode="Markdown")
        return

    # Extract genre, quality, and link from the end, and combine the rest as title
    *title_parts, genre, quality, link = args
    title = "_".join(title_parts)

    movie = get_movies().get(title, {})
    movie[quality] = link
    movie["genre"] = genre
    ref.child(title).set(movie)

    await update.message.reply_text(
        f"✅ Added *{title}* ({quality}, {genre})", parse_mode="Markdown"
    )


# /removemovie [partial title]
async def remove_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    if not context.args:
        await update.message.reply_text("Usage:\n/removemovie Title", parse_mode="Markdown")
        return

    query = ' '.join(context.args).lower()
    movies = get_movies()
    matched = [title for title in movies if query in title.lower()]

    if not matched:
        await update.message.reply_text("❌ No matches found.")
        return

    keyboard = [[InlineKeyboardButton(title, callback_data=f"remove|{title}")] for title in matched]
    await update.message.reply_text("🗑️ Choose movie to remove:", reply_markup=InlineKeyboardMarkup(keyboard))

# /updatemovie Title Quality Link
async def update_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Usage:\n/updatemovie Title Quality Link", parse_mode="Markdown")
        return

    title = args[0]
    quality = args[1]
    link = args[2]

    movie = get_movies().get(title)
    if not movie:
        await update.message.reply_text("❌ Movie not found.")
        return

    movie[quality] = link
    ref.child(title).set(movie)
    await update.message.reply_text(f"✅ Movie *{title}* updated with new link for {quality}.", parse_mode="Markdown")

# /admin command
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    await update.message.reply_text(
        "👑 *Admin Commands:*\n"
        "/addmovie Title Genre Quality Link\n"
        "/updatemovie Title Quality Link\n"
        "/removemovie Title",
        parse_mode="Markdown"
    )

# Webhook endpoint
@fastapi_app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return {"ok": True}

# Uptime check
@fastapi_app.get("/")
async def root():
    return {"status": "running"}

# Set webhook on startup
@fastapi_app.on_event("startup")
async def on_startup():
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        raise ValueError("WEBHOOK_URL is not set")

    await app.initialize()  # ✅ <-- Fix: initialize before processing updates
    await app.bot.set_webhook(webhook_url)
    print(f"✅ Webhook set to: {webhook_url}")

# Register handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("addmovie", add_movie))
app.add_handler(CommandHandler("removemovie", remove_movie))
app.add_handler(CommandHandler("updatemovie", update_movie))
app.add_handler(CommandHandler("admin", admin_panel))
app.add_handler(CallbackQueryHandler(button))

# Launch FastAPI server
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:fastapi_app", host="0.0.0.0", port=port)
