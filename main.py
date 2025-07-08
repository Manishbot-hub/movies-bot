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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
TOKEN = os.getenv("BOT_TOKEN")
FIREBASE_URL = os.getenv("FIREBASE_URL")
FIREBASE_KEY = json.loads(os.getenv("FIREBASE_KEY"))
SHRINKME_API = os.getenv("SHRINKME_API")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

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
user_reported_movies = {}
MOVIES_PER_PAGE = 10

def clean_firebase_key(key: str) -> str:
    """Sanitize Firebase keys by replacing disallowed characters."""
    return re.sub(r'[.#$/\[\]]', '_', key)



def _shorten_url_sync(link: str) -> str:
    """Synchronously call ShrinkMe to shorten a link."""
    url = f"https://shrinkme.io/api?api={SHRINKME_API}&url={link}"
    logging.info(f"Shortener called: {url}")

    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()

        try:
            data = resp.json()
            logging.info(f"Shortener response: {data}")
            if data.get("status") == "success" and data.get("shortenedUrl"):
                return data["shortenedUrl"]
        except json.JSONDecodeError:
            logging.warning("Shortener response was not JSON. Response text: %s", resp.text)

    except Exception as e:
        logging.warning(f"Shortener request failed: {e}")

    return link  # fallback


async def shorten_link(link: str) -> str:
    """Async wrapper: runs the sync function in a thread."""
    return await asyncio.to_thread(_shorten_url_sync, link)



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

def safe_callback_data(prefix: str, identifier: str) -> str:
    """
    Safely generate callback_data for Telegram buttons.
    Ensures the data is <= 64 bytes, and strips unsafe characters.
    """
    combined = f"{prefix}|{identifier}".replace("\n", " ").strip()
    return combined[:60]  # Trim to avoid Telegram's 64-byte limit

async def handle_title_or_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

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
        except Exception:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"[REPORT] {title.replace('_', ' ')}\nUser: {user_id}\nReason: {reason}"
           )

        except Exception as e:
            logging.warning(f"❌ Failed to notify admin: {e}")
        return

    # ✏️ Handle title rename
    if "edit_title_old" in context.user_data:
        return await handle_new_title(update, context)

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



async def upload_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Not authorized.")

    if context.bot_data.get("upload_running", False):
        return await update.message.reply_text("⚠️ Upload already in progress. Try again later.")
    
    context.bot_data["upload_running"] = True

    try:
        print("🚀 /uploadbulk triggered")

        doc = update.message.document
        if not doc or not doc.file_name.lower().endswith('.txt'):
            return await update.message.reply_text("⚠️ Please send a valid .txt file after /uploadbulk.")

        try:
            file_obj = await doc.get_file()
            content = await file_obj.download_as_bytearray()
            text = content.decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"❌ File read error: {e}")
            return await update.message.reply_text("❌ Failed to download or read the file.")

        lines = text.strip().splitlines()
        print(f"📄 Read {len(lines)} lines from uploaded file")

        # ✅ Counters
        added_count = 0
        skipped_count = 0
        failed_count = 0
        total_lines = len(lines)

        for line in lines:
            line = line.strip()
            if not line:
                continue

            parts = line.split()
            if len(parts) >= 3 and parts[-2].endswith("p") and parts[-1].startswith("http"):
                title = " ".join(parts[:-2])
                quality = parts[-2]
                link = parts[-1]
            else:
                failed_count += 1
                continue

            safe_key = clean_firebase_key(title)
            movie = ref.child(safe_key).get() or {}

            if quality in movie:
                skipped_count += 1
                continue

            try:
                short_url = await asyncio.to_thread(_shorten_url_sync, link)
                ref.child(safe_key).update({quality: short_url})
                added_count += 1
            except Exception as e:
                failed_count += 1

        # ✅ Summary message
        summary = (
            f"✅ *Upload Summary:*\n\n"
            f"• ✅ Successfully uploaded: *{added_count}*\n"
            f"• ⚠️ Skipped (already exists): *{skipped_count}*\n"
            f"• ❌ Failed to upload/shorten: *{failed_count}*\n"
            f"• 🧾 Total lines processed: *{total_lines}*"
        )
        await update.message.reply_text(summary, parse_mode="Markdown")

    finally:
        context.bot_data["upload_running"] = False



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
        short_url = await asyncio.to_thread(_shorten_url_sync, link)
        ref.child(safe_key).update({quality: short_url})
        return await send_temp_log(context, update.effective_chat.id,
            f"✅ Added: {title}  {quality}  {short_url}")
    except Exception as e:
        return await send_temp_log(context, update.effective_chat.id,
            f"❌ Failed: {title}  {quality} — error shortening or saving link")





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





async def search_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "edit_title_old" in context.user_data:
        return  # ⛔ User is editing a title, don't trigger search

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
        [InlineKeyboardButton(key.replace("_", " "), callback_data=safe_callback_data("movie", key))]
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

    keyboard = [[InlineKeyboardButton(title.replace("_", " "), callback_data=safe_callback_data("movie", title))] for title in current_page]

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton("◀ Back", callback_data=safe_callback_data("back", str(offset - MOVIES_PER_PAGE))))
    if end < len(movies):
        nav_buttons.append(InlineKeyboardButton("▶ Show More", callback_data=safe_callback_data("more", str(end))))

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
    buttons.append([InlineKeyboardButton("\u26A0\uFE0F Report Broken Link", callback_data=safe_callback_data("report", title))])

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
telegram_app.add_handler(CommandHandler("search", search_movie))  # Still works for /search
telegram_app.add_handler(CommandHandler("removemovie", remove_movie))
telegram_app.add_handler(CommandHandler("admin", admin_panel))
telegram_app.add_handler(CommandHandler("movies", list_movies))
telegram_app.add_handler(CommandHandler("edittitle", edittitle_command))
telegram_app.add_handler(CommandHandler("cleantitles", clean_titles))
telegram_app.add_handler(CommandHandler("removeall", remove_all_movies))

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
