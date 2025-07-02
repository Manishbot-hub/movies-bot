import os
import json
import firebase_admin
from firebase_admin import credentials, db

# Load Firebase credentials and URL from environment variables
firebase_key_json = os.getenv("FIREBASE_KEY")
firebase_url = os.getenv("FIREBASE_URL")

if not firebase_key_json or not firebase_url:
    raise ValueError("Missing FIREBASE_KEY or FIREBASE_URL in environment variables")

# Parse the Firebase key JSON
firebase_key = json.loads(firebase_key_json)

# Initialize Firebase only if not already initialized
if not firebase_admin._apps:
    cred = credentials.Certificate(firebase_key)
    firebase_admin.initialize_app(cred, {"databaseURL": firebase_url})


from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import requests
from fastapi import FastAPI, Request
import uvicorn

# 🔑 Load Firebase credentials from env
firebase_key = json.loads(os.getenv("FIREBASE_KEY"))
FIREBASE_URL = os.getenv("FIREBASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
ADRINOLINKS_API_TOKEN = os.getenv("ADRINOLINKS_API_TOKEN")




app = Application.builder().token(BOT_TOKEN).build()
fastapi_app = FastAPI()

# ✅ Link Shortener
def shorten_link(original_link):
    try:
        response = requests.get(f"https://adrinolinks.com/api?api={ADRINOLINKS_API_TOKEN}&url={original_link}")
        data = response.json()
        return data["shortenedUrl"] if data.get("status") == "success" else original_link
    except Exception as e:
        print(f"Link shortener error: {e}")
        return original_link

# ✅ Fetch all movies
def get_movies():
    return ref.get() or {}

# ✅ /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Movie World!\n\n"
        "Commands:\n"
        "/search <keyword>\n"
    )

# ✅ /search
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).lower()
    movies = get_movies()
    results = [title for title in movies if query in title.lower()]
    if results:
        buttons = [
            [InlineKeyboardButton(title, callback_data=f"movie_{title}")]
            for title in results
        ]
        await update.message.reply_text("🔎 Results:", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.message.reply_text("❌ No matching movies found.")

# ✅ Inline button
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("movie_"):
        title, quality = data[6:].split("|", 1)
        movies = get_movies()
        movie = movies.get(title, {})
        link = movie.get(quality)
        if link:
            await context.bot.send_message(chat_id=query.message.chat.id, text=f"🎬 *{title}* ({quality}):\n{link}", parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=query.message.chat.id, text="❌ Link not found.")

        # ✅ Delete the old message with buttons
        try:
            await query.message.delete()
        except Exception as e:
            print(f"Error deleting message: {e}")

    elif data.startswith("remove_"):
        title = data[7:]
        ref.child(title).delete()
        await query.edit_message_text(f"✅ *{title}* has been removed.", parse_mode="Markdown")

    elif data.startswith("report_"):
        title, quality = data[7:].split("|", 1)
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"🚨 User @{query.from_user.username or query.from_user.id} reported a broken link for:\n*{title}* ({quality})", parse_mode="Markdown")
        await query.edit_message_text("✅ Thanks! The admin has been notified.", parse_mode="Markdown")


# ✅ /addmovie
async def addmovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Usage:\n/addmovie Title Quality Link")
        return

    *title_parts, quality, link = args
    title = "_".join(title_parts)
    link = shorten_link(link)
    movies = get_movies()
    movie = movies.get(title, {})
    movie[quality] = link
    ref.child(title).set(movie)
    await update.message.reply_text(f"✅ Added *{title}* ({quality})", parse_mode="Markdown")

# ✅ /uploadbulk
async def uploadbulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    lines = update.message.text.split("\n")[1:]
    added = 0
    for line in lines:
        try:
            parts = line.strip().split("|")
            if len(parts) == 3:
                title, quality, link = parts
                title = title.strip()
                quality = quality.strip()
                link = shorten_link(link.strip())
                movies = get_movies()
                movie = movies.get(title, {})
                movie[quality] = link
                ref.child(title).set(movie)
                added += 1
        except Exception as e:
            print(f"Bulk upload error: {e}")

    await update.message.reply_text(f"✅ Bulk upload complete: {added} movies added.")

# ✅ /removemovie
async def removemovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    query = " ".join(context.args).lower()
    movies = get_movies()
    matches = [title for title in movies if query in title.lower()]

    if not matches:
        await update.message.reply_text("❌ No matching movies found.")
        return

    buttons = [
        [InlineKeyboardButton(f"❌ {title}", callback_data=f"delete_{title}")]
        for title in matches
    ]
    await update.message.reply_text(
        "Select movie to delete:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

# ✅ Button delete movie
async def delete_movie_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("delete_"):
        title = query.data.replace("delete_", "")
        ref.child(title).delete()
        await query.edit_message_text(f"✅ Deleted: *{title}*", parse_mode="Markdown")


# ✅ /report broken link
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage:\n/report Movie Title")
        return

    report_text = " ".join(context.args)
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🚩 *Broken Link Report*\n\nUser: {update.effective_user.username}\nMovie: {report_text}",
        parse_mode="Markdown",
    )
    await update.message.reply_text("✅ Your report has been sent to admin.")

# ✅ /admin
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    await update.message.reply_text(
        "**Admin Commands:**\n\n"
        "/addmovie Title Quality Link\n"
        "/uploadbulk\n"
        "/removemovie Partial_Title\n"
        "/report Movie_Title\n",
        parse_mode="Markdown"
    )

# ✅ Register handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("addmovie", addmovie))
app.add_handler(CommandHandler("uploadbulk", uploadbulk))
app.add_handler(CommandHandler("removemovie", removemovie))
app.add_handler(CommandHandler("report", report))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(CallbackQueryHandler(delete_movie_button, pattern=r"^delete_"))

# ✅ FastAPI root check
@fastapi_app.get("/")
async def root():
    return {"status": "Bot is running"}

# ✅ Webhook endpoint
@fastapi_app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    await app.process_update(Update.de_json(data, app.bot))
    return {"ok": True}

# ✅ Startup webhook setup
@fastapi_app.on_event("startup")
async def on_startup():
    if not WEBHOOK_URL:
        raise ValueError("WEBHOOK_URL is not set.")
    await app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    print("✅ Webhook set.")

# ✅ Uvicorn run
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:fastapi_app", host="0.0.0.0", port=port)
