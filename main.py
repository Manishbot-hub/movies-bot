import os
import json
import asyncio
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
import firebase_admin
from firebase_admin import credentials, db
from fastapi import FastAPI
import uvicorn

# Load Firebase credentials from environment
firebase_key = json.loads(os.getenv("FIREBASE_KEY"))
FIREBASE_URL = os.getenv("FIREBASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

cred = credentials.Certificate(firebase_key)
firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_URL})
ref = db.reference("movies")

app = Application.builder().token(BOT_TOKEN).build()
fastapi_app = FastAPI()

async def shorten_link(original_link):
    api_token = os.getenv("ADRINOLINKS_API_TOKEN")
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://adrinolinks.com/api?api={api_token}&url={original_link}") as resp:
            data = await resp.json()
            return data.get("shortenedUrl", original_link)

def get_movies():
    return ref.get() or {}

@app.command_handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to *Movies Bot*!\n\nUse /search, /report, or other commands.", parse_mode="Markdown"
    )

@app.command_handler
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage:\n/search keyword", parse_mode="Markdown")
        return

    keyword = " ".join(args).lower()
    movies = get_movies()
    found = []

    for title, qualities in movies.items():
        if keyword in title.lower():
            links = "\n".join(f"- `{q}`: {l}" for q, l in qualities.items() if q != "reports")
            found.append(f"*{title}*\n{links}")

    if found:
        await update.message.reply_text("\n\n".join(found), parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ No matches found.")

@app.command_handler
async def addmovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Usage:\n/addmovie Title Quality Link", parse_mode="Markdown")
        return

    *title_parts, quality, original_link = args
    title = " ".join(title_parts)
    link = await shorten_link(original_link)

    movie = get_movies().get(title, {})
    movie[quality] = link
    ref.child(title).set(movie)

    await update.message.reply_text(f"✅ Added *{title}* ({quality})", parse_mode="Markdown")

@app.command_handler
async def uploadbulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    if not context.args:
        await update.message.reply_text("❌ Send movie lines in this format:\n\nTitle | Quality | Link")
        return

    text = " ".join(context.args)
    lines = text.split('\n')
    added = 0

    for line in lines:
        try:
            title, quality, original_link = [x.strip() for x in line.split('|')]
            link = await shorten_link(original_link)
            movie = get_movies().get(title, {})
            movie[quality] = link
            ref.child(title).set(movie)
            added += 1
        except Exception:
            continue

    await update.message.reply_text(f"✅ Bulk upload done. Added {added} movies.")

@app.command_handler
async def removemovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    if not context.args:
        await update.message.reply_text("Usage:\n/removemovie keyword")
        return

    keyword = " ".join(context.args).lower()
    movies = get_movies()
    matches = [t for t in movies if keyword in t.lower()]

    if not matches:
        await update.message.reply_text("❌ No movies matched.")
        return

    buttons = [
        [InlineKeyboardButton(t, callback_data=f"del:{t}")] for t in matches
    ]
    await update.message.reply_text("Select movie to delete:", reply_markup=InlineKeyboardMarkup(buttons))

@app.callback_query_handler
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("del:"):
        title = query.data[4:]
        ref.child(title).delete()
        await query.edit_message_text(f"✅ Deleted *{title}*", parse_mode="Markdown")

@app.command_handler
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage:\n/report MovieTitle")
        return

    title = " ".join(context.args)
    movie = get_movies().get(title)

    if not movie:
        await update.message.reply_text("❌ Movie not found.")
        return

    reports = movie.get("reports", 0) + 1
    movie["reports"] = reports
    ref.child(title).set(movie)

    await update.message.reply_text(f"✅ Report received for *{title}*.\n(Reports: {reports})", parse_mode="Markdown")

@app.command_handler
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return

    cmds = [
        "/addmovie Title Quality Link",
        "/uploadbulk",
        "/removemovie keyword",
        "/admin",
    ]
    await update.message.reply_text("*Admin Commands:*\n\n" + "\n".join(cmds), parse_mode="Markdown")

@fastapi_app.get("/")
async def root():
    return {"status": "Bot is running"}

@fastapi_app.post("/webhook")
async def webhook(req):
    body = await req.body()
    await app.process_update(Update.de_json(json.loads(body), app.bot))
    return {"ok": True}

@fastapi_app.on_event("startup")
async def on_startup():
    if not WEBHOOK_URL:
        raise ValueError("WEBHOOK_URL is not set.")
    await app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:fastapi_app", host="0.0.0.0", port=port)
