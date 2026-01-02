import os
import time
import difflib
import requests
import re
import httpx
import json
import asyncio
import logging
import firebase_admin
import urllib3
import sys
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Image, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
import requests
import tempfile
from PIL import Image
from io import BytesIO
from telegram.helpers import escape_markdown
from datetime import datetime
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


import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)
TOKEN = os.getenv("BOT_TOKEN")
FIREBASE_URL = os.getenv("FIREBASE_URL")
FIREBASE_KEY = json.loads(os.getenv("FIREBASE_KEY"))
LINKPAY_API = os.getenv("LINKPAY_API")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
TMDB_TOKEN = os.getenv("TMDB_TOKEN", "")  # put your TMDB v4 token in Railway env
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"


if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_KEY)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_URL})

ref = db.reference("movies")
app = FastAPI()
telegram_app = Application.builder().token(TOKEN).build()

user_last_bot_message = {}
pending_reports = {}  # user_id -> title_being_reported
last_user_message_time = {}
user_movie_offset = {}  # For pagination
movie_requests = {}  # user_id -> timestamp for rate limiting
user_reported_movies = {}
MOVIES_PER_PAGE = 10
missing_posters_offset = {}
POSTERS_PER_PAGE = 10
missing_year_offset = {}
MISSING_YEAR_PER_PAGE = 50
GETFILEID_MODE = {}




def save_user_if_not_exists(update, context):
    """
    Save Telegram user info under Users/{user_id}
    ONLY if the user does not already exist.
    """
    user = update.effective_user
    if not user:
        return

    user_id = str(user.id)

    user_ref = db.reference("Users").child(user_id)
    existing = user_ref.get()

    if existing:
        # User already exists → do nothing
        return

    user_data = {
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "joined_at": datetime.utcnow().isoformat()
    }

    user_ref.set(user_data)


def ensure_user_saved(update, context):
    """
    Safe wrapper so user saving never breaks handlers.
    """
    try:
        save_user_if_not_exists(update, context)
    except Exception:
        pass

def clean_firebase_key(key: str) -> str:
    """Sanitize Firebase keys by replacing disallowed characters."""
    return re.sub(r'[.#$/\[\]]', '_', key)


def _linkpay_shorten_url_sync(link: str) -> str:
    """Synchronously call LinkPay to shorten a link."""
    API_KEY = os.getenv("LINKPAY_API")  # <-- Railway env var
    if not API_KEY:
        logging.error("❌ LINKPAY_API missing in Railway variables!")
        return link  # fallback if no API key found

    url = "https://linkpays.in/api"
    params = {
        "api": API_KEY,
        "url": link
    }

    logging.info(f"LinkPay shortener called: {url} | params={params}")

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()

        try:
            data = resp.json()
            logging.info(f"LinkPay response: {data}")

            # LinkPay success response examples may be:
            # {"status":"success","shortenedUrl":"https://linkpays.in/xxxxx"}
            # OR
            # {"shortUrl":"https://linkpays.in/xxxxx"}

            if data.get("shortenedUrl"):
                return data["shortenedUrl"]

            if data.get("shortUrl"):
                return data["shortUrl"]

        except json.JSONDecodeError:
            logging.warning(f"Invalid JSON from LinkPay: {resp.text}")

    except Exception as e:
        logging.error(f"LinkPay shortener request failed: {e}")

    return link  # fallback (send long link)


async def linkpay_shorten_link(link: str) -> str:
    """Async wrapper: runs the sync function in a thread."""
    return await asyncio.to_thread(_linkpay_shorten_url_sync, link)

def get_movies():
    return ref.get() or {}

def find_existing_title_case_insensitive(new_title: str, all_movies: dict) -> str | None:
    new_title_normalized = new_title.strip().lower()
    for existing_title in all_movies.keys():
        if existing_title.strip().lower() == new_title_normalized:
            return existing_title  # Return the actual Firebase key
    return None


async def delete_last(user_id, context):
    if user_id in user_last_bot_message:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=user_last_bot_message[user_id])
        except:
            pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_saved(update, context)

    # Delete previous bot messages (your existing behavior)
    await delete_last(user.id, context)

    text = (
        "👋 *Welcome to Movies World!*\n\n"
        "🎦 Type any movie name to get your favourite movies.\n"
        "📂 Use /movies to browse the full collection.\n"
        "🎫 Use /requestmovie to request a movie.\n\n"
        "👇 Choose an option:"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 How to Download Movies", callback_data="how_to_download")]
    ])

    msg = await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    user_last_bot_message[user.id] = msg.message_id


def safe_callback_data(prefix: str, identifier: str) -> str:
    """
    Safely generate callback_data for Telegram buttons.
    Ensures the data is <= 64 bytes, and strips unsafe characters.
    """
    combined = f"{prefix}|{identifier}".replace("\n", " ").strip()
    return combined[:60]  # Trim to avoid Telegram's 64-byte limit

async def handle_title_or_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user_saved(update, context)
    
    # 🛡️ Rate-limit to avoid flood
    now = time.time()
    if user_id in last_user_message_time:
        elapsed = now - last_user_message_time[user_id]
        if elapsed < 2:
            return
    last_user_message_time[user_id] = now

    # ✏️ Handle report reason input
    if user_id in pending_reports:
        title = pending_reports.pop(user_id)
        reason = update.message.text.strip()
        if not update.message or not update.message.text:
           return

        if not reason or len(reason) < 3:
            await update.message.reply_text("⚠️ Report reason too short. Report canceled.")
            return

        user_reported_movies.setdefault(user_id, set()).add(title)
        await update.message.reply_text("✅ Thanks! Your report has been sent to the admin.")

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ *New Broken Link Report*\n🎬 *{title.replace('_',' ')}*\n👤 User: `{user_id}`\n📝 Reason: _{reason}_",
                parse_mode="Markdown"
           )
        except Exception as e:
            logging.warning(f"❌ Failed to notify admin: {e}")
        return

    # ✏️ Handle title rename
    if "edit_title_old" in context.user_data:
        return await handle_new_title(update, context)

    # 🎬 Handle movie request
    if context.user_data.get("awaiting_movie_request"):
        context.user_data.pop("awaiting_movie_request")
        movie_title = update.message.text.strip()

        if not movie_title or len(movie_title) < 3:
            await update.message.reply_text("❌ Invalid request. Please try again with a proper title.")
            return

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        user = update.effective_user
        request_key = f"{user.username or user.id}_{timestamp}"

        db.reference("Requests").child(request_key).set({
            "title": movie_title,
            "user": {
                "id": user.id,
                "username": user.username,
                "first_name": user.first_name,
            },
            "timestamp": timestamp
        })

        await update.message.reply_text("✅ Your movie request has been sent to the admin. Thanks!")
        return

    if "awaiting_poster_url_for" in context.user_data:
        title = context.user_data.pop("awaiting_poster_url_for")
        url = update.message.text.strip()

        if not url.startswith("http"):
            return await update.message.reply_text("❌ Invalid URL. Try again.")

        ref.child(clean_firebase_key(title)).child("meta").update({"poster": url})
        return await update.message.reply_text("✅ Poster updated successfully!")

    # 🔍 Fallback to movie search
    await delete_last(user_id, context)
    return await search_movie(update, context)






async def send_temp_log(context, chat_id, text):
    msg = await context.bot.send_message(chat_id=chat_id, text=text)

    async def delete_later():
        await asyncio.sleep(10)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
        except:
            pass

    asyncio.create_task(delete_later())


# ✅ Properly placed helper (not nested!)
async def send_temp_log_rate_limited(context, chat_id, text, delay=1):
    """Rate-limited log sender to avoid Telegram flood errors."""
    try:
        msg = await context.bot.send_message(chat_id=chat_id, text=text)
        await asyncio.sleep(delay)  # delay between messages
        asyncio.create_task(delete_after_delay(context, chat_id, msg.message_id))
    except Exception as e:
        logging.warning(f"Send failed: {e}")


async def delete_after_delay(context, chat_id, message_id, delay=10):
    """Deletes a message after delay."""
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logging.warning(f"Delete failed: {e}")

async def request_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # ⏳ Anti-spam: 5 min cooldown
    now = time.time()
    if user_id in movie_requests and now - movie_requests[user_id] < 300:
        return await update.message.reply_text("⏳ Please wait before sending another request.")

    movie_requests[user_id] = now
    context.user_data["awaiting_movie_request"] = True

    await update.message.reply_text("🎬 Please type the name of the movie you want to request:")    

def clean_firebase_key(name: str):
    name = name.strip()
    name = name.replace("’", "'")
    name = name.replace("“", '"').replace("”", '"')
    name = name.replace("…", "...")
    name = " ".join(name.split())  # remove extra spaces
    return name



async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin check
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Not authorized.")

    if not context.args:
        return await update.message.reply_text(
            "Usage:\n/broadcast Your message here"
        )

    message = " ".join(context.args)

    users = db.reference("Users").get()
    if not users:
        return await update.message.reply_text("❌ No users found.")

    sent = 0
    failed = 0

    status = await update.message.reply_text("📤 Broadcasting message...")

    for user_id in users.keys():
        try:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=message
            )
            sent += 1
        except Exception:
            failed += 1

    await status.edit_text(
        f"✅ Broadcast completed!\n\n"
        f"📨 Sent: {sent}\n"
        f"❌ Failed: {failed}"
    )











async def upload_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        logger.warning("UNAUTHORIZED USER ATTEMPTED /uploadbulk")
        return await update.message.reply_text("⛔ Not authorized.")

    if context.bot_data.get("upload_running", False):
        logger.warning("UPLOAD ALREADY RUNNING")
        return await update.message.reply_text("⚠️ Upload already in progress. Try again later.")
    context.bot_data["upload_running"] = True

    try:
        doc = update.message.document
        if not doc or not doc.file_name.lower().endswith('.txt'):
            logger.warning("INVALID FILE SENT (not .txt)")
            return await update.message.reply_text(
                "⚠️ Please send a valid .txt file after /uploadbulk."
            )

        file_obj = await doc.get_file()
        content = await file_obj.download_as_bytearray()
        text = content.decode("utf-8", errors="ignore")

        lines = text.strip().splitlines()
        total_lines = len(lines)

        logger.info(f"UPLOAD STARTED | TOTAL LINES = {total_lines}")

        await update.message.reply_text(
            f"📄 Received `.txt` file with {total_lines} lines.\n⏳ Starting upload...",
            parse_mode="Markdown"
        )

        # Load existing movies once
        movies = get_movies()

        success_count = 0
        exists_count = 0
        failed_count = 0
        invalid_count = 0

        for idx, line in enumerate(lines, start=1):
            line = line.strip()

            if not line:
                continue

            logger.info(f"LINE {idx} | RAW: {line}")

            parts = line.split()
            if len(parts) >= 3 and parts[-2].endswith("p") and parts[-1].startswith("http"):
                title = " ".join(parts[:-2])
                quality = parts[-2]
                link = parts[-1]
            else:
                invalid_count += 1
                logger.warning(f"INVALID LINE {idx} | {line}")
                continue

            existing_key = find_existing_title_case_insensitive(title, movies)
            safe_key = clean_firebase_key(existing_key if existing_key else title)
            movie = movies.get(safe_key, {})

            if quality in movie:
                exists_count += 1
                logger.info(f"SKIPPED (EXISTS) | {safe_key} | {quality}")
                continue

            try:
                short_url = await asyncio.to_thread(_linkpay_shorten_url_sync, link)
                ref.child(safe_key).update({quality: short_url})

                # Ensure date_added exists
                meta_ref = ref.child(safe_key).child("meta")
                existing_meta = meta_ref.get() or {}

                if "date_added" not in existing_meta:
                    meta_ref.update({"date_added": int(time.time())})

                # 🔥 IMPORTANT: update in-memory cache to prevent false FAILED
                movies.setdefault(safe_key, {})[quality] = short_url

                success_count += 1
                logger.info(f"UPLOADED | {safe_key} | {quality}")

            except Exception as e:
                failed_count += 1
                logger.error(
                    f"FAILED LINE {idx} | {safe_key} | {quality} | ERROR: {repr(e)}"
                )
                continue

            # Progress log
            if idx % 10 == 0 or idx == total_lines:
                logger.info(f"PROGRESS {idx}/{total_lines}")
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"⏳ Processing movies: `{idx}/{total_lines}`",
                    parse_mode="Markdown"
                )
                await asyncio.sleep(0.3)

        logger.info(
            f"UPLOAD FINISHED | Total={total_lines} | "
            f"Success={success_count} | Exists={exists_count} | "
            f"Failed={failed_count} | Invalid={invalid_count}"
        )

        summary = (
            f"✅ *Upload Complete!*\n"
            f"• Total: {total_lines}\n"
            f"• Uploaded: {success_count}\n"
            f"• Already Exists: {exists_count}\n"
            f"• Failed: {failed_count}\n"
            f"• Invalid Lines: {invalid_count}"
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=summary,
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.exception("UNEXPECTED ERROR DURING UPLOAD")
        await update.message.reply_text("❌ Something went wrong during upload.")

    finally:
        context.bot_data["upload_running"] = False
        logger.info("UPLOAD FLAG RESET")




async def add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Not authorized.")

    text = update.message.text.replace("/addmovie", "").strip()
    lines = text.split("\n")

    # Detect input format
    if len(lines) == 2:
        title_quality_line, link = lines
    else:
        parts = text.split()
        if len(parts) < 3:
            return await send_temp_log(context, update.effective_chat.id,
                "❌ Invalid format.\nUse:\nTitle  Quality  Link\nor\nTitle  Quality\nLink")
        title_quality_line = " ".join(parts[:-1])
        link = parts[-1]

    match = re.match(r"(.+?)\s{2,}(\d{3,4}p)", title_quality_line)
    if not match:
        return await send_temp_log(context, update.effective_chat.id,
            "❌ Couldn't parse title and quality. Use double space between them.")

    title, quality = match.groups()
    safe_key = clean_firebase_key(title)
    movie = ref.child(safe_key).get() or {}

    if quality in movie:
        return await send_temp_log(context, update.effective_chat.id,
            f"⚠️ Skipped: {title}  {quality} already exists")

    try:
        short_url = await asyncio.to_thread(_linkpay_shorten_url_sync, link)
        ref.child(safe_key).update({quality: short_url})
        return await send_temp_log(context, update.effective_chat.id,
            f"✅ Added: {title}  {quality}  {short_url}")
    except Exception as e:
        return await send_temp_log(context, update.effective_chat.id,
            f"❌ Failed: {title}  {quality} — error shortening or saving link")







async def view_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    requests_ref = db.reference("Requests")
    requests_data = requests_ref.get()

    if not requests_data:
        return await update.message.reply_text("❌ No movie requests found.")

    reply_lines = []
    for key, info in requests_data.items():
        title = escape_markdown(info.get("title", "Unknown"), version=2)
        user = escape_markdown(str(info.get("user", "Unknown")), version=2)
        timestamp = escape_markdown(info.get("timestamp", ""), version=2)
        reply_lines.append(f"• *{title}* \\(User: `{user}`\\)")

    reply_text = "\n".join(reply_lines)
    await update.message.reply_text(
        f"*📂 Movie Requests:*\n\n{reply_text}",
        parse_mode="MarkdownV2"
    )

async def show_user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return await update.message.reply_text("⛔ Not authorized.")

    try:
        users_ref = db.reference("Users")
        all_users = users_ref.get() or {}
        count = len(all_users)
        await update.message.reply_text(f"👥 Total users: {count}")
    except Exception as e:
        await update.message.reply_text("❌ Error reading user stats.")
        logging.warning(f"Failed to fetch user stats: {e}")



async def scan_posters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin only: go through all movies and fetch poster for missing ones."""
    if update.effective_user.id != ADMIN_ID:
        return

    movies_ref = db.reference("movies")
    movies = movies_ref.get() or {}

    missing = []
    for title, data in movies.items():
        meta = (data or {}).get("meta") or {}
        if not meta.get("poster"):   # no poster saved yet
            missing.append(title)

    if not missing:
        await update.message.reply_text("✅ All movies already have posters saved.")
        return

    await update.message.reply_text(
        f"🖼 Found {len(missing)} movies/series without posters.\n"
        f"Starting TMDB scan… (this may take a bit)."
    )

    updated = 0
    for title in missing:
        await ensure_poster_for_movie(title)
        updated += 1
        # small sleep to be nice with TMDB
        if updated % 10 == 0:
            await asyncio.sleep(1)

    await update.message.reply_text(f"✅ Poster scan finished. Updated {updated} titles.")

async def list_missing_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Not authorized.")

    user_id = update.effective_user.id
    missing_year_offset[user_id] = 0
    await show_missing_year_page(update, context)

async def show_missing_year_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query if hasattr(update, "callback_query") and update.callback_query else None
    message = query.message if query else update.message
    user_id = message.chat.id
    movies = db.reference("movies").get() or {}
    missing = []

    for title, data in movies.items():
        meta = (data or {}).get("meta") or {}
        if "meta" not in data or not meta.get("year"):
            missing.append(title)

    if not missing:
        return await message.reply_text("🎯 All movies/series have a release year.")

    missing_sorted = sorted(missing)

    offset = missing_year_offset.get(user_id, 0)
    end = offset + MISSING_YEAR_PER_PAGE
    current_page = missing_sorted[offset:end]

    escaped_lines = []
    for t in current_page:
        clean = escape_markdown(t.replace("_", " "), version=2)
        escaped_lines.append(f"• {clean}")

    text = (
        "🎬 *Missing Release Year*\n\n"
        + "\n".join(escaped_lines)
        + f"\n\n📍 Showing {offset+1}–{min(end,len(missing_sorted))} of {len(missing_sorted)}"
    )

    text = text.replace(".", "\\.")  # Escape dots

    keyboard = []
    nav = []

    if offset > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data="year_prev"))
    if end < len(missing_sorted):
        nav.append(InlineKeyboardButton("➡ Next", callback_data="year_next"))
    if nav:
        keyboard.append(nav)

    await message.reply_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def remove_all_movies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return await update.message.reply_text("⛔ Not authorized.")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm Delete All", callback_data="confirm_delete_all")]
    ])
    await update.message.reply_text(
        "⚠️ Are you sure you want to delete *ALL* movies? This cannot be undone!",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def edittitle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Not authorized.")

    query = update.message.text.replace("/edittitle", "").strip().lower()
    if not query:
        return await update.message.reply_text("❌ Please provide part of the movie title to search.")

    movies = get_movies()
    matches = [
        title for title in movies
        if query in title.lower()
    ]

    if not matches:
        return await update.message.reply_text("❌ No matching movies found.")

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = [
    [InlineKeyboardButton(title, callback_data=safe_callback_data("edit_title_select", title))]
    for title in matches[:10]
]


    await update.message.reply_text(
        "🎯 Select the movie whose title you want to edit:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_new_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "edit_title_old" not in context.user_data:
        return  # Ignore unrelated messages

    new_title = update.message.text.strip()
    old_title = context.user_data.pop("edit_title_old")

    old_key = clean_firebase_key(old_title)
    new_key = clean_firebase_key(new_title)

    movie = ref.child(old_key).get()
    if not movie:
        return await update.message.reply_text("❌ Original movie not found.")

    ref.child(new_key).set(movie)
    ref.child(old_key).delete()

    await send_temp_log(
        context, update.effective_chat.id,
        f"✅ Title updated:\n`{old_title}` → `{new_title}`"
    )

async def missing_posters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Not authorized.")

    user_id = update.effective_user.id
    missing_posters_offset[user_id] = 0
    await show_missing_page(update, context)

async def fixposter_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Not authorized.")

    # format → /fixposter Movie Title
    args = context.args
    if not args:
        return await update.message.reply_text("Usage:\n/fixposter Movie Title")

    query = " ".join(args).lower()
    movies = get_movies()

    matches = [t for t in movies if query in t.lower()]
    if not matches:
        return await update.message.reply_text("❌ No matching movies found.")

    keyboard = [
        [InlineKeyboardButton(t.replace("_", " "), callback_data=f"fpselect|{t}")]
        for t in matches[:10]
    ]
    await update.message.reply_text(
        "🎯 Select the movie to update poster:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_missing_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query if hasattr(update, "callback_query") and update.callback_query else None
    message = query.message if query else update.message
    user_id = message.chat.id
    movies = get_movies()

    missing = []
    for t, d in movies.items():
        meta = d.get("meta", {})
        if not meta.get("poster"):
            missing.append(t)

    if not missing:
        return await update.message.reply_text("🎉 All movies have posters!")

    offset = missing_posters_offset.get(user_id, 0)
    end = offset + POSTERS_PER_PAGE
    current_page = missing[offset:end]

    keyboard = [[InlineKeyboardButton(t.replace("_", " "),callback_data=safe_callback_data("fixposter", t))]
        for t in current_page
    ]

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data="missing_prev"))
    if end < len(missing):
        nav.append(InlineKeyboardButton("➡ Next", callback_data="missing_next"))
    if nav:
        keyboard.append(nav)

    await message.reply_text(
        f"📌 Missing Posters {offset+1}-{min(end,len(missing))} of {len(missing)}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def clean_titles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Not authorized.")

    logging.info("✅ /cleantitles triggered")

    unwanted_words = [
        "download", "full movie", "watch", "online",
        "free", "movie", "hd", "bluray", "web-dl"
    ]

    movies = get_movies()
    cleaned = 0
    skipped = 0
    unchanged = 0
    changed_titles = []

    for key in list(movies.keys()):
        original_title = key
        cleaned_title = original_title

        for word in unwanted_words:
            cleaned_title = re.sub(rf"(?i)\b{re.escape(word)}\b", "", cleaned_title)

        cleaned_title = re.sub(r"\s{2,}", " ", cleaned_title).strip()

        if not cleaned_title or cleaned_title == original_title:
            unchanged += 1
            continue

        new_key = clean_firebase_key(cleaned_title)

        if ref.child(new_key).get():
            logging.info(f"⚠️ Skipped (exists): {cleaned_title}")
            skipped += 1
            continue

        try:
            ref.child(new_key).set(movies[original_title])
            ref.child(original_title).delete()
            logging.info(f"✅ Renamed: {original_title} → {cleaned_title}")
            changed_titles.append(f"{original_title} → {cleaned_title}")
            cleaned += 1
        except Exception as e:
            logging.error(f"❌ Failed to clean {original_title}: {e}")

    summary = f"✅ Clean complete:\n• Renamed: {cleaned}\n• Skipped: {skipped}\n• Unchanged: {unchanged}"
    await update.message.reply_text(summary)

    if changed_titles:
        preview = "\n".join(changed_titles[:20])
        await update.message.reply_text(f"*Changed Titles:*\n\n{preview}", parse_mode="Markdown")

async def fix_movie_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("Not allowed.")

    args = context.args
    if len(args) < 2:
        return await update.message.reply_text("Usage:\n/fixposter MovieTitle URL")

    title = " ".join(args[:-1])
    url = args[-1]
    key = clean_firebase_key(title)

    ref = db.reference("movies")
    if not ref.child(key).get():
        return await update.message.reply_text("Movie not found.")

    ref.child(key).child("meta").update({"poster": url})
    await update.message.reply_text("Poster updated! 👌")

def extract_title_and_year(raw_title: str) -> tuple[str, str | None]:
    """
    Try to get a clean title + year (if present) from the Firebase title.
    Works for titles with or without year.
    """
    # find first 4-digit year like 1999, 2024 etc.
    m = re.search(r"(19|20)\d{2}", raw_title)
    year = m.group(0) if m else None

    clean_title = raw_title
    if year:
        # remove '(2024)' / '[2024]' / '2024' from title string
        clean_title = re.sub(r"(\(|\[)?\s*" + year + r"\s*(\)|\])?", "", clean_title).strip()

    # small clean-ups
    clean_title = clean_title.replace("S01", "").replace("S1", "").strip(" -:()[]")
    return clean_title, year


def _fetch_tmdb_meta_sync(title: str, year: str | None) -> dict | None:
    """
    Blocking TMDB call (runs in thread). Returns meta dict or None.
    """
    if not TMDB_TOKEN:
        logging.warning("TMDB_TOKEN missing, skip poster fetch")
        return None

    params = {
        "query": title,
        "include_adult": "false",
        "page": 1,
    }
    if year:
        params["year"] = year

    headers = {
        "Authorization": f"Bearer {TMDB_TOKEN}",
        "Accept": "application/json",
    }

    try:
        resp = requests.get(
            f"{TMDB_BASE_URL}/search/multi",
            params=params,
            headers=headers,
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if not results:
            logging.info(f"TMDB: no results for '{title}' ({year})")
            return None

        # prefer movie/tv results
        picked = None
        for r in results:
            if r.get("media_type") in ("movie", "tv"):
                picked = r
                break
        if picked is None:
            picked = results[0]

        poster_path = picked.get("poster_path")
        poster_url = TMDB_IMAGE_BASE + poster_path if poster_path else None

        if picked.get("media_type") == "tv":
            date = picked.get("first_air_date") or ""
        else:
            date = picked.get("release_date") or ""

        found_year = date[:4] if len(date) >= 4 else None
        if not found_year:
            found_year = year

        meta = {
            "tmdb_id": picked.get("id"),
            "poster": poster_url,
            "year": found_year,
            "is_series": picked.get("media_type") == "tv",
            "tmdb_title": picked.get("name") or picked.get("title") or title,
        }
        logging.info(f"TMDB meta for '{title}': {meta}")
        return meta

    except Exception as e:
        logging.warning(f"TMDB fetch failed for '{title}' ({year}): {e}")
        return None



def create_movies_pdf_range(movies_slice, output_file):
    c = canvas.Canvas(output_file, pagesize=A4)
    width, height = A4

    for title, data in movies_slice:
        meta = data.get("meta", {})
        poster_url = meta.get("poster")

        # Title
        c.setFont("Helvetica-Bold", 18)
        c.drawString(40, height - 50, title)

        # Poster
        if poster_url:
            try:
                img_data = requests.get(poster_url, timeout=15).content
                img = ImageReader(BytesIO(img_data))

                img_width = width - 80
                img_height = img_width * 1.5

                c.drawImage(
                    img,
                    40,
                    height - 80 - img_height,
                    width=img_width,
                    height=img_height,
                    preserveAspectRatio=True,
                )
            except:
                c.setFont("Helvetica", 12)
                c.drawString(40, height - 100, "⚠️ Poster failed to load")
        else:
            c.drawString(40, height - 100, "❌ No poster available")

        c.showPage()

    c.save()


# ------------------ add below create_movies_pdf_range ------------------

def get_movies_added_today():
    """
    Return a list of (title, data) for movies added in the last 24 hours,
    preserving Firebase natural order (same as get_movies().items()).
    """
    movies = get_movies()  # dict in Firebase order
    movie_list = list(movies.items())

    now = int(time.time())
    one_day = 86400  # seconds in 24 hours

    today_movies = [
        (title, data) for title, data in movie_list
        if data.get("meta", {}).get("date_added", 0) > now - one_day
    ]
    return today_movies




async def fetch_tmdb_meta_for_title(title: str):
    import difflib

    original_title = title

    # Clean before search
    cleaned = re.sub(r'\bS\d{1,2}\b', '', title, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bPart\s?\d+\b', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\b(480p|720p|1080p|2160p|WEB[- ]?DL|Bluray|Hindi|Dual Audio)\b',
                     '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    # Extract year
    year_match = re.search(r'(19|20)\d{4}', cleaned)
    year_match = re.search(r'\((\d{4})\)$', cleaned)
    input_year = year_match.group(1) if year_match else None
    if input_year:
        cleaned = cleaned.replace(f"({input_year})", "").strip()

    # Detect series season (S01, S02)
    is_series_title = bool(re.search(r"S\d{1,2}", title, re.IGNORECASE))

    headers = {
        "Authorization": f"Bearer {TMDB_TOKEN}",
        "Accept": "application/json",
    }

    params = {
        "query": cleaned,
        "include_adult": "false",
    }
    if input_year:
        params["year"] = input_year

    try:
        resp = requests.get(f"{TMDB_BASE_URL}/search/multi",
                            headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            return None

        best_match = None
        best_score = 0

        for r in results:
            tmdb_type = r.get("media_type")
            tmdb_title = r.get("title") or r.get("name") or ""
            tmdb_year = (r.get("release_date") or r.get("first_air_date") or "")[:4]

            # --- STRICT FILTERS ---

            # Type filter
            if is_series_title and tmdb_type != "tv":
                continue
            if not is_series_title and tmdb_type != "movie":
                continue

            # Year filter (if year exists)
            if input_year and tmdb_year and tmdb_year != input_year:
                continue

            # Title similarity
            score = difflib.SequenceMatcher(None,
                                            cleaned.lower(),
                                            tmdb_title.lower()).ratio() * 100

            if score >= 80 and score > best_score:
                best_score = score
                best_match = r

        if not best_match:
            return None

        poster_url = None
        if best_match.get("poster_path"):
            poster_url = TMDB_IMAGE_BASE + best_match["poster_path"]

        return {
            "poster": poster_url,
            "tmdb_id": best_match.get("id"),
            "tmdb_title": best_match.get("name") or best_match.get("title"),
            "year": input_year or tmdb_year,
            "is_series": best_match.get("media_type") == "tv",
        }

    except Exception as e:
        logging.error(f"TMDB failed {title}: {e}")
        return None




async def ensure_poster_for_movie(key: str, force: bool = False):
    movie_ref = db.reference("movies").child(key)
    data = movie_ref.get() or {}

    meta = data.get("meta") or {}

    # Skip if already has poster and not force
    if meta.get("poster") and not force:
        return

    tmdb_meta = await fetch_tmdb_meta_for_title(key)
    if not tmdb_meta:
        return

    # Save MAIN poster for Movie or entire Series
    movie_ref.child("meta").update({
        "poster": tmdb_meta.get("poster"),
        "is_series": tmdb_meta.get("is_series", False),
        "tmdb_id": tmdb_meta.get("tmdb_id"),
        "year": tmdb_meta.get("year"),
        "tmdb_title": tmdb_meta.get("tmdb_title"),
    })

    # If not a series → stop here
    if not tmdb_meta.get("is_series"):
        return

    # Extract Seasons from Keys (Quality lines remain untouched)
    season_keys = [k for k in data.keys() if re.match(r"S\d{1,2}", k)]

    if not season_keys:
        return

    headers = {
        "Authorization": f"Bearer {TMDB_TOKEN}",
        "Accept": "application/json",
    }

    tmdb_id = tmdb_meta.get("tmdb_id")

    for season_key in season_keys:
        season_num = int(re.findall(r"\d+", season_key)[0])

        try:
            r = requests.get(
                f"{TMDB_BASE_URL}/tv/{tmdb_id}/season/{season_num}",
                headers=headers,
                timeout=10,
            )
            r.raise_for_status()
            season_data = r.json()
            poster_path = season_data.get("poster_path")

            if poster_path:
                poster_url = TMDB_IMAGE_BASE + poster_path
                movie_ref.child(season_key).update({"poster": poster_url})

        except Exception:
            continue

async def search_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user_saved(update, context)
    if "edit_title_old" in context.user_data:
        return  # user editing title, skip search

    user_id = update.effective_user.id
    await delete_last(user_id, context)

    # Get text
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

    # substring match
    substring_matches = [
        key for key, name in normalized.items() if query in name
    ]

    # fuzzy match if needed
    if substring_matches:
        final_matches = substring_matches
    else:
        close = difflib.get_close_matches(query, normalized.values(), n=10, cutoff=0.5)
        final_matches = [k for k, v in normalized.items() if v in close]

    if not final_matches:
        msg = await update.message.reply_text("❌ No matching movies found.")
        user_last_bot_message[user_id] = msg.message_id
        return

    # Build keyboard
    keyboard = []

    for title in final_matches:
        safe = clean_firebase_key(title)
        safe = re.sub(r'[^a-zA-Z0-9_\-]', '', safe)
        safe = safe[:50]

        keyboard.append([
            InlineKeyboardButton(
                title.replace("_", " "),
                callback_data=f"movie|{safe}"
            )
        ])

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

    keyboard = []

    for title in current_page:
        safe = clean_firebase_key(title)
        safe = re.sub(r'[^a-zA-Z0-9_\-]', '', safe)
        safe = safe[:50]

        keyboard.append([
            InlineKeyboardButton(
                title.replace("_", " "),
                callback_data=f"movie|{safe}"
            )
        ])

    nav_buttons = []

    if offset > 0:
        nav_buttons.append(
            InlineKeyboardButton("◀ Back", callback_data=safe_callback_data("back", str(offset - MOVIES_PER_PAGE)))
        )

    if end < len(movies):
        nav_buttons.append(
            InlineKeyboardButton("▶ Show More", callback_data=safe_callback_data("more", str(end)))
        )

    if nav_buttons:
        keyboard.append(nav_buttons)

    msg = await send_func(
        f"🎬 Showing movies {offset + 1} to {min(end, len(movies))} of {len(movies)}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    user_last_bot_message[user_id] = msg.message_id



async def show_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await delete_last(query.from_user.id, context)

    # safe key from callback
    _, safe = query.data.split("|", 1)

    movies = get_movies()

    movie = None
    real_title = None

    # 1) direct match (if Firebase key was already safe)
    if safe in movies:
        movie = movies[safe]
        real_title = safe

    # 2) match by cleaning Firebase titles the SAME way
    if not movie:
        for title, data in movies.items():
            cleaned = clean_firebase_key(title)
            cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "", cleaned)
            cleaned = cleaned[:50]

            if cleaned == safe:
                movie = data
                real_title = title
                break

    # 3) still not found
    if not movie:
        msg = await query.message.reply_text("❌ Movie not found.")
        user_last_bot_message[query.from_user.id] = msg.message_id
        return

    # Fetch poster if missing
    await ensure_poster_for_movie(real_title, force=False)

    meta = movie.get("meta", {})
    poster = meta.get("poster")
    year = meta.get("year")

    caption = f"*{real_title}*"
    if year:
        caption += f" ({year})"
    caption += "\n\nSelect quality 👇"

    # Quality buttons
    buttons = [
        [InlineKeyboardButton(f"{q} 🔗", url=l)]
        for q, l in movie.items()
        if q != "meta"
    ]

    # Report button
    buttons.append([
        InlineKeyboardButton(
            "⚠️ Report Broken Link",
            callback_data=safe_callback_data("report", real_title)
        )
    ])

    markup = InlineKeyboardMarkup(buttons)

    # Send poster if exists
    if poster:
        msg = await query.message.reply_photo(
            photo=poster,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=markup
        )
    else:
        msg = await query.message.reply_text(
            caption,
            parse_mode="Markdown",
            reply_markup=markup
        )

    user_last_bot_message[query.from_user.id] = msg.message_id



async def getpdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Must pass range like: /getpdf 1-100
    if not context.args:
        return await update.message.reply_text(
            "⚠️ Please specify range.\nExample: `/getpdf 1-100`",
            parse_mode="Markdown"
        )

    # Parse range
    try:
        rng = context.args[0]
        start_str, end_str = rng.split("-")
        start = int(start_str)
        end = int(end_str)
    except:
        return await update.message.reply_text(
            "❌ Invalid format.\nUse: `/getpdf 1-100`",
            parse_mode="Markdown"
        )

    # Load movies (Firebase order)
    movies = list(get_movies().items())
    total = len(movies)

    # Validate range
    if start < 1 or end > total or start > end:
        return await update.message.reply_text(
            f"❌ Invalid range.\nThere are only *{total}* movies.",
            parse_mode="Markdown"
        )

    # Slice movies
    movie_slice = movies[start-1 : end]

    # Notify user
    loading = await update.message.reply_text(
        f"⏳ Creating PDF for movies {start}-{end}..."
    )

    # Create PDF file
    pdf_path = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf").name
    create_movies_pdf_range(movie_slice, pdf_path)

    # Send PDF
    await update.message.reply_document(
        document=open(pdf_path, "rb"),
        filename=f"movies_{start}-{end}.pdf",
        caption=f"📄 Movies {start}-{end}"
    )

    os.remove(pdf_path)

    try:
        await loading.delete()
    except:
        pass


# ------------------ add near other command handlers (below getpdf) ------------------

async def getpdfrecent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage: /getpdfrecent 1-400
    Generates a PDF containing only movies that were added in the last 24 hours,
    sliced by the requested range.
    """
    if not context.args:
        return await update.message.reply_text(
            "⚠️ Please specify range of today's movies.\nExample: `/getpdfrecent 1-400`",
            parse_mode="Markdown"
        )

    # Parse range
    try:
        rng = context.args[0]
        start_str, end_str = rng.split("-")
        start = int(start_str)
        end = int(end_str)
    except:
        return await update.message.reply_text(
            "❌ Invalid format.\nUse: `/getpdfrecent 1-400`",
            parse_mode="Markdown"
        )

    # Get only today's movies (Firebase natural order)
    today_movies = get_movies_added_today()
    total = len(today_movies)

    if total == 0:
        return await update.message.reply_text("ℹ️ No movies were added in the last 24 hours.")

    if start < 1 or end > total or start > end:
        return await update.message.reply_text(
            f"❌ Invalid range.\nThere are only *{total}* movies added today.",
            parse_mode="Markdown"
        )

    # Slice and create PDF
    movie_slice = today_movies[start-1 : end]

    loading = await update.message.reply_text(f"⏳ Creating PDF for today's movies {start}-{end}...")
    pdf_path = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf").name
    create_movies_pdf_range(movie_slice, pdf_path)

    await update.message.reply_document(
        document=open(pdf_path, "rb"),
        filename=f"today_{start}-{end}.pdf",
        caption=f"📄 Movies added today ({start}-{end})"
    )

    try:
        os.remove(pdf_path)
    except:
        pass

    try:
        await loading.delete()
    except:
        pass



async def remove_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("\u26D4 Notauthorized.")
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

    keyboard = [[InlineKeyboardButton(title.replace("_", " "), callback_data=safe_callback_data("delete", title))] for title in matches]
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

        if user_id in user_reported_movies and title in user_reported_movies[user_id]:
            await query.edit_message_text("⚠️ You've already reported this movie.")
            return

        pending_reports[user_id] = title
        await query.edit_message_text(
            f"📝 Please describe the problem with *{title.replace('_', ' ')}*.\n\n"
            f"Example: 'Wrong link', '404 not found', 'GDToT page blank', etc.",
            parse_mode="Markdown"
       )


    elif query.data == "how_to_download":
        # Replace this with your real Telegram file_id
        TUTORIAL_VIDEO = "BAACAgUAAxkBAAI0umlXNriiLqkrBv0rvS-37akO_o2HAALYIQACJaa4VkjoMpIvNIBWOAQ"

        await query.message.reply_video(
            video=TUTORIAL_VIDEO,
            caption="📹 Here's how to download movies step-by-step!"
        )
        await query.answer()
        return

    elif query.data.startswith("fixposter|"):
        title = query.data.split("|", 1)[1]
        context.user_data["fix_poster_title"] = title
        await query.message.reply_text(
            f"✏️ Send a correct title for poster fetch:\n`{title.replace('_',' ')}`",
            parse_mode="Markdown"
       )

    elif query.data.startswith("fpselect|"):
        title = query.data.split("|", 1)[1]
        context.user_data["awaiting_poster_url_for"] = title
        await query.message.reply_text(
            f"📌 Send poster URL for:\n{title.replace('_', ' ')}"
        )

    elif query.data == "missing_next":
        uid = query.from_user.id
        missing_posters_offset[uid] += POSTERS_PER_PAGE
        await query.message.delete()
        await show_missing_page(update, context)

    elif query.data == "missing_prev":
        uid = query.from_user.id
        missing_posters_offset[uid] = max(0, missing_posters_offset[uid] - POSTERS_PER_PAGE)
        await query.message.delete()
        await show_missing_page(update, context)

    elif query.data == "year_next":
        uid = query.from_user.id
        missing_year_offset[uid] += MISSING_YEAR_PER_PAGE
        await query.message.delete()
        await show_missing_year_page(update.callback_query, context)

    elif query.data == "year_prev":
        uid = query.from_user.id
        missing_year_offset[uid] = max(0, missing_year_offset[uid] - MISSING_YEAR_PER_PAGE)
        await query.message.delete()
        await show_missing_year_page(update.callback_query, context)
    
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
   
    elif query.data == "confirm_delete_all":
        ref.set({})  # Clears the 'movies' node
        await query.edit_message_text("✅ All movies have been deleted from the database.")

    elif query.data.startswith("edit_title_select|"):
        old_title = query.data.split("|", 1)[1]
        context.user_data["edit_title_old"] = old_title

        await query.message.reply_text(
            f"✏️ Send the new title for:\n`{old_title}`",
            parse_mode="Markdown"
        )
       
    


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
telegram_app.add_handler(CommandHandler("requestmovie", request_movie))
telegram_app.add_handler(CommandHandler("request", view_requests))
telegram_app.add_handler(CommandHandler("getpdf", getpdf))
telegram_app.add_handler(CommandHandler("getpdfrecent", getpdfrecent))
telegram_app.add_handler(CommandHandler("search", search_movie))  # Still works for /search
telegram_app.add_handler(CommandHandler("removemovie", remove_movie))
telegram_app.add_handler(CommandHandler("scanposters", scan_posters))
telegram_app.add_handler(CommandHandler("missingyear", list_missing_year))
telegram_app.add_handler(CommandHandler("missingposters", missing_posters))
telegram_app.add_handler(CommandHandler("fixposter", fixposter_command))
telegram_app.add_handler(CommandHandler("admin", admin_panel))
telegram_app.add_handler(CommandHandler("movies", list_movies))
telegram_app.add_handler(CommandHandler("edittitle", edittitle_command))
telegram_app.add_handler(CommandHandler("cleantitles", clean_titles))
telegram_app.add_handler(CommandHandler("removeall", remove_all_movies))
telegram_app.add_handler(CommandHandler("stats", show_user_stats))
telegram_app.add_handler(CommandHandler("broadcast", broadcast))
telegram_app.add_handler(MessageHandler(filters.Document.ALL, upload_bulk))
telegram_app.add_handler(CallbackQueryHandler(button_handler))

# ✅ Handles both title edit and general text search
telegram_app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_title_or_search))





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
# trigger redeploy
# trigger redeploy
# trigger redeploy
