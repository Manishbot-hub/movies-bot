import os
import httpx
import json
import asyncio
import logging
import firebase_admin
from firebase_admin import credentials, db
import requests

from fastapi import FastAPI, Request
import uvicorn
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
FIREBASE_URL = os.getenv("FIREBASE_URL")
FIREBASE_KEY = json.loads(os.getenv("FIREBASE_KEY"))
ADRINOLINKS_API_TOKEN = os.getenv("ADRINOLINKS_API_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# ✅ Prevent multiple Firebase initializations
if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_KEY)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_URL})

ref = db.reference("movies")


app = FastAPI()
telegram_app = Application.builder().token(TOKEN).build()
user_last_bot_message = {}

API_URL = "https://adrinolinks.in/api?api=3ffda80fb70a54aaf0bfea117a49710a89cd4192&url=yourdestinationlink.com&alias=CustomAlias&format=text"  # ✅ Replace with correct Adrinolinks API URL

async def shorten_link(link):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                API_URL,
                headers={"Authorization": f"Bearer {ADRINOLINKS_API_TOKEN}"},
                json={"url": link}
            )
            logging.info(f"Shorten API Status: {response.status_code}, Body: {response.text}")  # ✅ Log API response

            if response.status_code == 200:
                data = response.json()
                return data.get("shortenedUrl", link)  # ✅ Make sure this matches the actual API field name
            else:
                logging.error(f"Adrinolinks API Error: {response.status_code} {response.text}")
                return link
        except Exception as e:
            logging.error(f"Shorten link failed: {e}")
            return link

def get_movies():
    return ref.get() or {}

async def delete_last(user_id, context):
    if user_id in user_last_bot_message:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=user_last_bot_message[user_id])
        except:
            pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_last(update.effective_user.id, context)
    text = "👋 Welcome to Movies World! Use /addmovie, /uploadbulk, or /movies to browse."
    msg = await update.message.reply_text(text)
    user_last_bot_message[update.effective_user.id] = msg.message_id

async def add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Usage:\n/addmovie Title Quality Link", parse_mode="Markdown")
        return

    *title_parts, quality, original_link = args
    title = "_".join(title_parts)
    short_link = await shorten_link(original_link)  # ✅ FIXED HERE
    movie = get_movies().get(title, {})
    movie[quality] = short_link  # ✅ FIXED HERE
    ref.child(title).set(movie)

    await update.message.reply_text(f"✅ Added *{title}* ({quality})", parse_mode="Markdown")

async def upload_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    if not update.message.text:
        await update.message.reply_text("Send movies in text: Title | Quality | Link (one per line)")
        return

    lines = update.message.text.split("\n")
    added = 0
    for line in lines:
        try:
            title, quality, link = [x.strip() for x in line.split("|")]
            short_link = await shorten_link(original_link)  # ✅ Correct
            movie = get_movies().get(title, {})
            movie[quality] = short_link
            ref.child(title).set(movie)
            added += 1
        except:
            continue

    await update.message.reply_text(f"✅ Bulk upload complete: {added} movies added.")
    
async def search_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await delete_last(user_id, context)

    args = context.args
    if not args:
        msg = await update.message.reply_text("Usage:\n/search keyword")
        user_last_bot_message[user_id] = msg.message_id
        return

    query = " ".join(args).lower()
    movies = get_movies()
    matches = [title for title in movies if query in title.lower()]

    if not matches:
        msg = await update.message.reply_text("❌ No matching movies found.")
        user_last_bot_message[user_id] = msg.message_id
        return

    keyboard = [
        [InlineKeyboardButton(title.replace("_", " "), callback_data=f"movie|{title}")]
        for title in matches
    ]

    msg = await update.message.reply_text(
        f"🔎 Found {len(matches)} matching movie(s):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    user_last_bot_message[user_id] = msg.message_id

async def list_movies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_last(update.effective_user.id, context)
    movies = get_movies()
    if not movies:
        msg = await update.message.reply_text("📭 No movies found.")
        user_last_bot_message[update.effective_user.id] = msg.message_id
        return

    text = "🎬 *Movies List:*\n\n"
    keyboard = []
    for title in movies.keys():
        keyboard.append([InlineKeyboardButton(title.replace("_", " "), callback_data=f"movie|{title}")])

    msg = await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    user_last_bot_message[update.effective_user.id] = msg.message_id

async def show_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await delete_last(query.from_user.id, context)

    _, title = query.data.split("|", 1)
    movie = get_movies().get(title)
    if not movie:
        msg = await query.message.reply_text("❌ Movie not found.")
        user_last_bot_message[query.from_user.id] = msg.message_id
        return

    text = f"*{title.replace('_', ' ')}*\n\n"
    buttons = []
    for quality, link in movie.items():
        buttons.append([InlineKeyboardButton(f"{quality} 🔗", url=link)])
    buttons.append([InlineKeyboardButton("⚠️ Report Broken Link", callback_data=f"report|{title}")])

    msg = await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
    user_last_bot_message[query.from_user.id] = msg.message_id

async def remove_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage:\n/removemovie partial_title")
        return

    query = " ".join(args).lower()
    movies = get_movies()
    matches = [t for t in movies if query in t.lower()]

    if not matches:
        await update.message.reply_text("❌ No matching movies.")
        return

    keyboard = [[InlineKeyboardButton(title.replace("_", " "), callback_data=f"delete|{title}")] for title in matches]
    await update.message.reply_text("Select movie to delete:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data.startswith("delete|"):
        _, title = query.data.split("|", 1)
        ref.child(title).delete()
        await query.edit_message_text(f"✅ Movie *{title.replace('_',' ')}* deleted.", parse_mode="Markdown")

    elif query.data.startswith("movie|"):
        await show_movie(update, context)

    elif query.data.startswith("report|"):
        _, title = query.data.split("|", 1)
        logging.warning(f"⚠️ User {user_id} reported broken link for movie: {title}")
        await query.edit_message_text("✅ Thanks for reporting! Admin will review.")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    commands = """
🛠️ *Admin Commands:*

/addmovie Title Quality Link
/uploadbulk
/removemovie Title
/admin
"""
    await update.message.reply_text(commands, parse_mode="Markdown")

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("addmovie", add_movie))
telegram_app.add_handler(CommandHandler("uploadbulk", upload_bulk))
telegram_app.add_handler(CommandHandler("search", search_movie))
telegram_app.add_handler(CommandHandler("removemovie", remove_movie))
telegram_app.add_handler(CommandHandler("admin", admin_panel))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, list_movies))
telegram_app.add_handler(CallbackQueryHandler(button_handler))

@app.on_event("startup")
async def on_startup():
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        raise ValueError("WEBHOOK_URL is not set.")
    await telegram_app.bot.set_webhook(webhook_url)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()

    # ✅ Check if app is already initialized
    if not telegram_app._initialized:
        await telegram_app.initialize()

    await telegram_app.process_update(Update.de_json(data, telegram_app.bot))
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"status": "Bot is running"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
