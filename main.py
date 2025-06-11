import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
import os

# Load movie list
with open("movies.json", "r") as file:
    MOVIES = json.load(file)

BOT_TOKEN = os.getenv("7236698980:AAEGZ-2MNv-jzG0tQch5EbnVoe6ESacFKLg")
if not BOT_TOKEN:7236698980:AAEGZ-2MNv-jzG0tQch5EbnVoe6ESacFKLg
    raise ValueError("BOT_TOKEN is not set")

# /start command - show all movies
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(title, callback_data=title)] for title in MOVIES.keys()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🎬 Choose a movie to download:", reply_markup=reply_markup)

# /search command - find matching movies
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗️ Please provide a search query. Example: /search inception")
        return

    query = ' '.join(context.args).lower()
    results = {title: link for title, link in MOVIES.items() if query in title.lower()}

    if not results:
        await update.message.reply_text("🔍 No movies found matching your query.")
        return

    keyboard = [
        [InlineKeyboardButton(title, callback_data=title)] for title in results.keys()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"🔍 Search results for \"{query}\":", reply_markup=reply_markup)

# Button click handler
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    movie_title = query.data
    link = MOVIES.get(movie_title)
    await query.message.reply_text(
        f"🎬 {movie_title}\n📥 [Download here]({link})",
        parse_mode='Markdown'
    )

# Run bot
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CallbackQueryHandler(button))
    import asyncio

async def main():
    await app.initialize()
    await app.start()
    await app.bot.set_webhook("https://your-render-url.onrender.com/")  # replace with your actual URL
    await asyncio.Event().wait()

asyncio.run(main())

