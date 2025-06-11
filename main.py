import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
import os

# Load movie list with qualities
with open("movies.json", "r") as file:
    MOVIES = json.load(file)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set")

# /start command — show movie list
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"movie|{title}")]
        for title in MOVIES.keys()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🎬 Choose a movie to download:", reply_markup=reply_markup)

# /search command — search movie titles
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗️ Please provide a search query. Example: /search inception")
        return

    query = ' '.join(context.args).lower()
    results = {title: links for title, links in MOVIES.items() if query in title.lower()}

    if not results:
        await update.message.reply_text("🔍 No movies found matching your query.")
        return

    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"movie|{title}")]
        for title in results.keys()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"🔍 Search results for "{query}":", reply_markup=reply_markup)

# Handle button taps
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("movie|"):
        movie_title = data.split("|")[1]
        movie_data = MOVIES.get(movie_title)

        if isinstance(movie_data, dict):
            keyboard = [
                [InlineKeyboardButton(q, callback_data=f"quality|{movie_title}|{q}")]
                for q in movie_data.keys()
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text(f"🎥 Choose quality for *{movie_title}*:", parse_mode="Markdown", reply_markup=reply_markup)
        else:
    await query.message.reply_text(
    f"🎬 {movie_title} ({quality})\n📥 [Download here]({link})",
    parse_mode='Markdown'
)


 elif data.startswith("quality|"):
    _, movie_title, quality = data.split("|")
    movie_links = MOVIES.get(movie_title, {})
    link = movie_links.get(quality)

    if link:
        await query.message.reply_text(
            f"🎬 {movie_title} ({quality})\n📥 [Download here]({link})",
            parse_mode='Markdown'
        )
    else:
        await query.message.reply_text("❌ Link not found for this quality.")


# Run bot
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CallbackQueryHandler(button))
    app.run_polling()
