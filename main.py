import json
import os
import asyncio
import requests
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

# Firebase setup with singleton check
firebase_key = json.loads(os.getenv("FIREBASE_KEY_JSON", "{}"))
firebase_url = os.getenv("FIREBASE_DB_URL")

if not firebase_admin._apps:
    cred = credentials.Certificate(firebase_key)
    firebase_admin.initialize_app(cred, {"databaseURL": firebase_url})

ref = db.reference("movies")


# Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set")

# Admin ID
ADMIN_ID = int(os.getenv("ADMIN_ID", "6301044201"))

# Adrinolinks API Token
ADRN_API_TOKEN = os.getenv("ADRN_API_TOKEN")

app = ApplicationBuilder().token(BOT_TOKEN).build()
fastapi_app = FastAPI()

def get_movies():
    try:
        return ref.get() or {}
    except Exception as e:
        print("Firebase error:", e)
        return {}

def shorten_link(link):
    try:
        api_url = f"https://adrinolinks.com/api?api={ADRN_API_TOKEN}&url={link}"
        response = requests.get(api_url).json()
        return response.get("shortenedUrl", link)
    except Exception as e:
        print("Link Shortener Error:", e)
        return link

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 Welcome to MovieBot!\nUse /search to find movies or /admin for admin panel.")

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
        [InlineKeyboardButton(k, callback_data=f"movie|{k}")]
        for k in results
    ]
    await update.message.reply_text("🔍 Results:", reply_markup=InlineKeyboardMarkup(keyboard))

# Buttons
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
                [InlineKeyboardButton(q, url=movie[q])] for q in movie
            ]
            await query.message.reply_text(
                f"🎬 *{title}*\nChoose quality:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif data.startswith("remove|"):
            title = data.split("|")[1]
            ref.child(title).delete()
            await query.message.reply_text(f"🗑️ Movie *{title}* removed.", parse_mode="Markdown")
    except Exception as e:
        print("Button Error:", e)

# /addmovie
async def add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Usage:\n/addmovie Title Quality Link", parse_mode="Markdown")
        return
    *title_parts, quality, link = args
    title = "_".join(title_parts)
    short_link = shorten_link(link)
    movie = get_movies().get(title, {})
    movie[quality] = short_link
    ref.child(title).set(movie)
    await update.message.reply_text(f"✅ Added *{title}* ({quality})", parse_mode="Markdown")

# /removemovie
async def remove_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
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

# /updatemovie
async def update_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Usage:\n/updatemovie Title Quality Link", parse_mode="Markdown")
        return
    *title_parts, quality, link = args
    title = "_".join(title_parts)
    short_link = shorten_link(link)
    movie = get_movies().get(title)
    if not movie:
        await update.message.reply_text("❌ Movie not found.")
        return
    movie[quality] = short_link
    ref.child(title).set(movie)
    await update.message.reply_text(f"✅ Movie *{title}* updated with {quality}.", parse_mode="Markdown")

# /uploadbulk
async def upload_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return
    try:
        with open("movies_bulk.txt", "r") as f:
            lines = f.readlines()
        count = 0
        for line in lines:
            parts = line.strip().split(",")
            if len(parts) >= 3:
                title, quality, link = parts[0], parts[1], parts[2]
                short_link = shorten_link(link)
                movie = get_movies().get(title, {})
                movie[quality] = short_link
                ref.child(title).set(movie)
                count += 1
        await update.message.reply_text(f"✅ Bulk upload complete: {count} movies added.")
    except Exception as e:
        print("Bulk Upload Error:", e)
        await update.message.reply_text("❌ Bulk upload failed.")

# /report
async def report_broken(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage:\n/report MovieName", parse_mode="Markdown")
        return
    report = f"🚨 Broken Link Report:\nUser: [{update.effective_user.first_name}](tg://user?id={update.effective_user.id})\nMovie: {' '.join(context.args)}"
    await app.bot.send_message(chat_id=ADMIN_ID, text=report, parse_mode="Markdown")
    await update.message.reply_text("✅ Thanks! Admin has been notified.")

# /admin panel
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return
    await update.message.reply_text(
        "👑 *Admin Commands:*\n"
        "/addmovie Title Quality Link\n"
        "/updatemovie Title Quality Link\n"
        "/removemovie Title\n"
        "/uploadbulk\n"
        "/report MovieName",
        parse_mode="Markdown"
    )

# Webhook route
@fastapi_app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, app.bot)
        if not getattr(app, 'running', False):
            await app.initialize()
        await app.process_update(update)
    except Exception as e:
        print("❌ Webhook Error:", e)
    return {"ok": True}

# Uptime check
@fastapi_app.get("/")
async def root():
    return {"status": "running"}

# Startup: set webhook
@fastapi_app.on_event("startup")
async def on_startup():
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        raise ValueError("WEBHOOK_URL is not set")
    await app.bot.set_webhook(webhook_url)
    print(f"✅ Webhook set to: {webhook_url}")

# Register handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("addmovie", add_movie))
app.add_handler(CommandHandler("removemovie", remove_movie))
app.add_handler(CommandHandler("updatemovie", update_movie))
app.add_handler(CommandHandler("uploadbulk", upload_bulk))
app.add_handler(CommandHandler("report", report_broken))
app.add_handler(CommandHandler("admin", admin_panel))
app.add_handler(CallbackQueryHandler(button))

# Run server
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:fastapi_app", host="0.0.0.0", port=port)
