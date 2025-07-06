import os
import requests
import re
import httpx
import json
import asyncio
import logging
import firebase_admin
from firebase_admin import credentials, db
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

if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_KEY)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_URL})

ref = db.reference("movies")
app = FastAPI()
telegram_app = Application.builder().token(TOKEN).build()

user_last_bot_message = {}
user_movie_offset = {}  # For pagination
MOVIES_PER_PAGE = 10

def clean_firebase_key(key: str) -> str:
    """Sanitize Firebase keys by replacing disallowed characters."""
    return re.sub(r'[.#$/\[\]]', '_', key)

import requests

def _shorten_url_sync(link: str) -> str:
    api = ADRINOLINKS_API_TOKEN
    url = f"https://adrinolinks.in/st?api={api}&url={link}"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "success" and data.get("shortenedUrl"):
            return data["shortenedUrl"]
    except ValueError:
        # non‑JSON response
        logging.warning("Shortener returned non‑JSON, using original link")
    except Exception as e:
        logging.warning(f"Shortener error: {e}")
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
    text = "\U0001F44B Welcome to Movies World! Use /search to get your favourite movies🎦🎦, Type any movie name, or /movies to browse."
    msg = await update.message.reply_text(text)
    user_last_bot_message[update.effective_user.id] = msg.message_id

async def add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("\u26D4 Not authorized.")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Usage:\n/addmovie Title Quality Link", parse_mode="Markdown")
        return

    *title_parts, quality, original_link = args
    title = "_".join(title_parts)
    short_link = await asyncio.to_thread(_shorten_url_sync, original_link)
    movie = get_movies().get(title, {})
    movie[quality] = short_link
    ref.child(title).set(movie)

    await update.message.reply_text(f"\u2705 Added *{title}* ({quality})", parse_mode="Markdown")


async def upload_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # only admin
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Not authorized.")
    
    text = update.message.text or ""
    lines = text.splitlines()
    # strip off the command itself
    if lines and lines[0].startswith("/uploadbulk"):
        lines = lines[1:]
    
    added = 0
    i = 0
    total = len(lines)
    logging.info(f"Bulk upload: {total} lines to process")
    
    while i < total:
        line = lines[i].strip()
        # Try single‑line: Title Quality URL
        parts = line.split()
        if len(parts) >= 3 and parts[-1].startswith("http"):
            title   = " ".join(parts[:-2])
            quality = parts[-2]
            url     = parts[-1]
            i += 1
        # Otherwise two‑line: "Title Quality" then next line is URL
        elif i + 1 < total and lines[i+1].strip().startswith("http"):
            tparts = line.rsplit(" ", 1)
            if len(tparts) != 2:
                logging.warning(f"⚠️ Can't parse title/quality at line {i}: {line}")
                i += 1
                continue
            title, quality = tparts
            url = lines[i+1].strip()
            i += 2
        else:
            logging.warning(f"⚠️ Unrecognized format at line {i}: {line}")
            i += 1
            continue

        # sanitize firebase key
        safe_title = clean_firebase_key(title)
        if not safe_title:
            logging.warning(f"⚠️ Empty title after sanitization: '{title}'")
            continue

        try:
            # shorten off main thread
            short_url = await asyncio.to_thread(_shorten_url_sync, url)
            movie = get_movies().get(safe_title, {})
            movie[quality] = short_url
            ref.child(safe_title).set(movie)
            added += 1
            logging.info(f"➕ Added '{title}' [{quality}] → {short_url}")
        except Exception as e:
            logging.warning(f"⚠️ Failed saving '{title}': {e}")

    await update.message.reply_text(f"✅ Bulk upload complete: {added} movie(s) added.")









async def search_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await delete_last(user_id, context)

    # Get the search query from text or command
    if update.message:
        query = update.message.text.strip().lower()
    else:
        args = context.args
        if not args:
            msg = await update.message.reply_text("Usage:\n/search keyword")
            user_last_bot_message[user_id] = msg.message_id
            return
        query = " ".join(args).strip().lower()

    movies = get_movies()
    normalized = {title: title.replace("_", " ").lower() for title in movies}

    # First try substring matching
    substring_matches = [
        key for key, name in normalized.items() if query in name
    ]

    # If no substring match, try fuzzy match
    fuzzy_matches = []
    if not substring_matches:
        all_titles = list(normalized.values())
        close_titles = difflib.get_close_matches(query, all_titles, n=10, cutoff=0.5)
        fuzzy_matches = [
            key for key, name in normalized.items() if name in close_titles
        ]

    final_matches = substring_matches or fuzzy_matches

    if not final_matches:
        msg = await update.message.reply_text("❌ No matching movies found.")
        user_last_bot_message[user_id] = msg.message_id
        return

    keyboard = [
        [InlineKeyboardButton(key.replace("_", " "), callback_data=f"movie|{key}")]
        for key in final_matches
    ]

    msg = await update.message.reply_text(
        f"🔍 Found {len(final_matches)} matching movie(s):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    user_last_bot_message[user_id] = msg.message_id
async def list_movies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await delete_last(user_id, context)
    user_movie_offset[user_id] = 0
    await show_movie_page(user_id, context, update.message.reply_text)

async def show_movie_page(user_id, context, send_func):
    movies = list(get_movies().keys())
    offset = user_movie_offset.get(user_id, 0)
    end = offset + MOVIES_PER_PAGE
    current_page = movies[offset:end]

    keyboard = [[InlineKeyboardButton(title.replace("_", " "), callback_data=f"movie|{title}")] for title in current_page]

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton("◀ Back", callback_data=f"back|{offset - MOVIES_PER_PAGE}"))
    if end < len(movies):
        nav_buttons.append(InlineKeyboardButton("▶ Show More", callback_data=f"more|{end}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    msg = await send_func(
        f"\U0001F3AC Showing movies {offset + 1} to {min(end, len(movies))} of {len(movies)}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    user_last_bot_message[user_id] = msg.message_id

async def show_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await delete_last(query.from_user.id, context)

    _, title = query.data.split("|", 1)
    movie = get_movies().get(title)
    if not movie:
        msg = await query.message.reply_text("\u274C Movie not found.")
        user_last_bot_message[query.from_user.id] = msg.message_id
        return

    text = f"*{title.replace('_', ' ')}*\n\n"
    buttons = [[InlineKeyboardButton(f"{quality} \U0001F517", url=link)] for quality, link in movie.items()]
    buttons.append([InlineKeyboardButton("\u26A0\uFE0F Report Broken Link", callback_data=f"report|{title}")])

    msg = await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
    user_last_bot_message[query.from_user.id] = msg.message_id

async def remove_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("\u26D4 Not authorized.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage:\n/removemovie partial_title")
        return

    query = " ".join(args).lower()
    movies = get_movies()
    matches = [t for t in movies if query in t.lower()]

    if not matches:
        await update.message.reply_text("\u274C No matching movies.")
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
        await query.edit_message_text(f"\u2705 Movie *{title.replace('_',' ')}* deleted.", parse_mode="Markdown")

    elif query.data.startswith("movie|"):
        await show_movie(update, context)

    elif query.data.startswith("report|"):
        _, title = query.data.split("|", 1)
        logging.warning(f"\u26A0\uFE0F User {user_id} reported broken link for movie: {title}")
        await query.edit_message_text("\u2705 Thanks for reporting! Admin will review.")

    elif query.data.startswith("more|"):
        _, new_offset = query.data.split("|", 1)
        user_movie_offset[user_id] = int(new_offset)
        await delete_last(user_id, context)
        await show_movie_page(user_id, context, query.message.reply_text)

    elif query.data.startswith("back|"):
        _, new_offset = query.data.split("|", 1)
        user_movie_offset[user_id] = max(0, int(new_offset))
        await delete_last(user_id, context)
        await show_movie_page(user_id, context, query.message.reply_text)

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
telegram_app.add_handler(CommandHandler("movies", list_movies))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_movie))
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
