# main.py
import os
import json
import firebase_admin
from firebase_admin import credentials, db
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# Firebase setup
cred = credentials.Certificate("firebase_key.json")
db_url = os.getenv("FIREBASE_DB_URL")
firebase_admin.initialize_app(cred, {
    "databaseURL": db_url
})
ref = db.reference("movies")

# Bot setup
BOT_TOKEN = os.getenv("BOT_TOKEN")
app = ApplicationBuilder().token(BOT_TOKEN).build()
fastapi_app = FastAPI()

ADMIN_ID = 6301044201

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome to *Movies Bot*!\nUse /search to find movies.", parse_mode="Markdown")

# /search command
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = ' '.join(context.args).lower()
    movies = ref.get() or {}
    results = {title: link for title, link in movies.items() if query in title.lower()}

    if not results:
        await update.message.reply_text("🔍 No matching movies found.")
        return

    keyboard = [[InlineKeyboardButton(title, callback_data=f"movie|{title}")] for title in results]
    await update.message.reply_text("🔍 Results:", reply_markup=InlineKeyboardMarkup(keyboard))

# /addmovie (admin only)
async def add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        title, quality, link = context.args[0], context.args[1], context.args[2]
        current = ref.get() or {}
        if title in current:
            current[title][quality] = link
        else:
            current[title] = {quality: link}
        ref.set(current)
        await update.message.reply_text(f"✅ Movie *{title}* ({quality}) added!", parse_mode="Markdown")
    except:
        await update.message.reply_text("Usage: /addmovie Title Quality Link")

# Button handler
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    movies = ref.get() or {}

    if data.startswith("movie|"):
        title = data.split("|")[1]
        options = movies.get(title, {})
        if isinstance(options, dict):
            keyboard = [[InlineKeyboardButton(q, callback_data=f"quality|{title}|{q}")] for q in options]
            await query.message.reply_text(f"🎥 Choose quality for *{title}*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("quality|"):
        _, title, quality = data.split("|")
        link = movies.get(title, {}).get(quality)
        if link:
            await query.message.reply_text(f"🎬 {title} ({quality})\n📥 [Download here]({link})", parse_mode='Markdown')

# Register handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("addmovie", add_movie))
app.add_handler(CallbackQueryHandler(button))

# Webhook for FastAPI
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
async def on_start():
    webhook_url = "https://your-app-name.up.railway.app/webhook"
    await app.bot.set_webhook(webhook_url)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:fastapi_app", host="0.0.0.0", port=port)
