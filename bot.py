"""
Telegram bot for India Genius Challenge – Answer Collector Edition.
No typing commands – only inline buttons.
Allows importing a JSON file of answers and exporting today's answer key.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from io import BytesIO
from threading import Thread

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)
from telegram.error import Conflict

try:
    from telegram.constants import ParseMode
except ImportError:
    from telegram import ParseMode

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

logger = logging.getLogger("bot")
logger.info("━" * 60)
logger.info("Answer Collector Bot starting")
logger.info("━" * 60)

from genius_1780164377809 import (
    load_cache, save_cache, merged_quiz_cache,
    run_probe_attempt,
    QUIZ_KEY, load_probe_stats, save_probe_stats
)
from ai_solver import AIConfig, test_ai_connection

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

# Conversation states
WAITING_FOR_AI_PROVIDER = 1
WAITING_FOR_AI_KEY = 2
WAITING_FOR_IMPORT = 3

# ----------------------------------------------------------------------
# Helper: get today's answer key as JSON bytes
# ----------------------------------------------------------------------
def get_answer_key_json() -> tuple[bytes, str] | tuple[None, None]:
    cache = load_cache()
    today_cache = cache.get(QUIZ_KEY, {})
    if not today_cache:
        return None, None
    answer_list = [
        {"question_id": qid, "correct_answer": answer}
        for qid, answer in today_cache.items()
    ]
    json_str = json.dumps(answer_list, indent=2, ensure_ascii=False)
    filename = f"answers_{datetime.now().strftime('%Y-%m-%d')}.json"
    return json_str.encode("utf-8"), filename

# ----------------------------------------------------------------------
# Inline keyboard
# ----------------------------------------------------------------------
def main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📅 Get Today's Answer Key", callback_data="get_answers")],
        [InlineKeyboardButton("🔍 Collect New Answers (Probe)", callback_data="collect")],
        [InlineKeyboardButton("📥 Import Answer JSON", callback_data="import_json")],
        [InlineKeyboardButton("📊 Cache Statistics", callback_data="stats")],
        [InlineKeyboardButton("⚙️ Configure AI Provider", callback_data="config_ai")],
        [InlineKeyboardButton("🧪 Test AI Connection", callback_data="test_ai")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ----------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *India Genius Challenge – Answer Collector*\n\n"
        "I collect answers for the daily quiz using anonymous probes.\n"
        "Use the buttons below.\n\n"
        "📥 *Import JSON* – upload a file with `[{question_id, correct_answer}]`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu()
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "get_answers":
        await send_answer_key(query)
    elif data == "collect":
        await run_collection(query, context)
    elif data == "import_json":
        await start_import(query)
    elif data == "stats":
        await show_stats(query)
    elif data == "config_ai":
        await start_ai_config(query, context)
    elif data == "test_ai":
        await test_ai(query)
    else:
        await query.edit_message_text("❓ Unknown.", reply_markup=main_menu())

async def send_answer_key(query):
    json_bytes, filename = get_answer_key_json()
    if json_bytes is None:
        await query.edit_message_text(
            "❌ No answers collected yet.\nUse *Collect New Answers* or *Import JSON*.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu()
        )
        return
    await query.edit_message_text("📤 Sending...", parse_mode=ParseMode.MARKDOWN)
    await query.message.reply_document(
        document=BytesIO(json_bytes),
        filename=filename,
        caption=f"✅ Answer key for {datetime.now().strftime('%Y-%m-%d')}"
    )
    await query.message.reply_text("📋 Back to menu:", reply_markup=main_menu())

async def run_collection(query, context: ContextTypes.DEFAULT_TYPE):
    msg = await query.edit_message_text(
        "🔍 *Starting answer collection...* (may take minutes)",
        parse_mode=ParseMode.MARKDOWN
    )
    cache = load_cache()
    quiz_cache = merged_quiz_cache(cache)
    question_meta = dict(cache.get("question_meta", {}))
    tried_options = cache.get("tried_options", {})
    probe_stats = load_probe_stats()
    num_runs = 50
    total_learned = 0

    connector = aiohttp.TCPConnector(limit=100)
    async with aiohttp.ClientSession(connector=connector) as session:
        for i in range(1, num_runs + 1):
            learned = await run_probe_attempt(
                session, quiz_cache, question_meta, tried_options, i, probe_stats
            )
            total_learned += learned
            if i % 10 == 0:
                cache[QUIZ_KEY] = quiz_cache
                cache["question_meta"] = question_meta
                cache["tried_options"] = tried_options
                save_cache(cache)
                save_probe_stats(probe_stats)
            if i % 5 == 0:
                await msg.edit_text(
                    f"🔍 Collecting... ({i}/{num_runs})\n📚 +{total_learned} new | 🗂️ {len(quiz_cache)}",
                    parse_mode=ParseMode.MARKDOWN
                )
            await asyncio.sleep(3)
        cache[QUIZ_KEY] = quiz_cache
        cache["question_meta"] = question_meta
        cache["tried_options"] = tried_options
        save_cache(cache)
        save_probe_stats(probe_stats)

    await msg.edit_text(
        f"✅ *Collection finished!*\n📚 Learned {total_learned}\n🗂️ Total: {len(quiz_cache)}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu()
    )

async def show_stats(query):
    cache = load_cache()
    quiz_cache = merged_quiz_cache(cache)
    stats = load_probe_stats()
    questions_seen = len(stats.get("questions", {}))
    cached = len(quiz_cache)
    coverage = (cached / questions_seen * 100) if questions_seen > 0 else 0
    await query.edit_message_text(
        f"📊 *Cache Stats*\n\n"
        f"🗂️ Cached: `{cached}`\n"
        f"🔍 Seen: `{questions_seen}`\n"
        f"📈 Coverage: `{coverage:.1f}%`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu()
    )

# ----------------------------------------------------------------------
# Import JSON conversation
# ----------------------------------------------------------------------
async def start_import(query):
    await query.edit_message_text(
        "📥 *Import Answer JSON*\n\n"
        "Send me a JSON file with the following format:\n"
        "```\n[{\"question_id\": \"...\", \"correct_answer\": \"...\"}]\n```\n"
        "The answers will be merged into today's cache.\n"
        "Send /cancel to abort.",
        parse_mode=ParseMode.MARKDOWN
    )
    return WAITING_FOR_IMPORT

async def receive_import_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".json"):
        await update.message.reply_text("❌ Please send a .json file.")
        return WAITING_FOR_IMPORT
    try:
        file = await doc.get_file()
        content = await file.download_as_bytearray()
        data = json.loads(content.decode("utf-8"))
        if not isinstance(data, list):
            await update.message.reply_text("❌ JSON must be a list of objects.")
            return ConversationHandler.END
        cache = load_cache()
        today_cache = cache.get(QUIZ_KEY, {})
        imported = 0
        for item in data:
            qid = item.get("question_id")
            ans = item.get("correct_answer")
            if qid and ans:
                if qid not in today_cache or today_cache[qid] != ans:
                    today_cache[qid] = ans
                    imported += 1
        cache[QUIZ_KEY] = today_cache
        save_cache(cache)
        await update.message.reply_text(
            f"✅ Imported {imported} new answers.\nTotal today: {len(today_cache)}",
            reply_markup=main_menu()
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
    return ConversationHandler.END

# ----------------------------------------------------------------------
# AI Configuration (unchanged, but simplified)
# ----------------------------------------------------------------------
async def start_ai_config(query, context):
    keyboard = [
        [InlineKeyboardButton("Groq", callback_data="ai_provider_groq")],
        [InlineKeyboardButton("Gemini", callback_data="ai_provider_gemini")],
        [InlineKeyboardButton("OpenRouter", callback_data="ai_provider_openrouter")],
        [InlineKeyboardButton("Cancel", callback_data="cancel_ai_config")],
    ]
    await query.edit_message_text(
        "🤖 Select AI provider:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_FOR_AI_PROVIDER

async def ai_provider_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    provider = query.data.replace("ai_provider_", "")
    context.user_data["ai_provider"] = provider
    await query.edit_message_text(f"✅ Provider: {provider}\nSend your API key (or /cancel).")
    return WAITING_FOR_AI_KEY

async def receive_ai_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_key = update.message.text.strip()
    provider = context.user_data.get("ai_provider")
    if not provider:
        await update.message.reply_text("Error. Start over with /start.")
        return ConversationHandler.END
    cfg = AIConfig(provider=provider, api_key=api_key)
    cfg.save()
    await update.message.reply_text(f"✅ AI configured: {provider}", reply_markup=main_menu())
    return ConversationHandler.END

async def cancel_ai_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Cancelled.", reply_markup=main_menu())
    else:
        await update.message.reply_text("Cancelled.", reply_markup=main_menu())
    return ConversationHandler.END

async def test_ai(query):
    cfg = AIConfig.load()
    if not cfg.is_configured:
        await query.edit_message_text("❌ No AI configured.", reply_markup=main_menu())
        return
    await query.edit_message_text("🧪 Testing...")
    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        result = await test_ai_connection(cfg, session=session)
    status = "✅" if result["ok"] else "❌"
    text = f"{status} *Test*\nAnswer: {result['answer']}\nCorrect: {result['correct']}\nLatency: {result['latency']}s"
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())

# ----------------------------------------------------------------------
# HTTP health server
# ----------------------------------------------------------------------
def run_health_server():
    from http.server import HTTPServer, BaseHTTPRequestHandler
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(404)
        def log_message(self, *args, **kwargs):
            pass
    HTTPServer(("0.0.0.0", 8080), HealthHandler).serve_forever()

# ----------------------------------------------------------------------
# Error handler
# ----------------------------------------------------------------------
async def error_handler(update, context):
    if isinstance(context.error, Conflict):
        logger.critical("Another instance running. Exiting.")
        sys.exit(1)
    logger.error("Unhandled error: %s", context.error, exc_info=context.error)

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
async def main():
    Thread(target=run_health_server, daemon=True).start()
    logger.info("Health server on port 8080")

    app = Application.builder().token(TOKEN).build()
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CallbackQueryHandler(button_callback, pattern="^(get_answers|collect|stats|config_ai|test_ai|import_json)$"))
    app.add_handler(CallbackQueryHandler(ai_provider_callback, pattern="^ai_provider_"))
    app.add_handler(CallbackQueryHandler(cancel_ai_config, pattern="^cancel_ai_config$"))

    # Import conversation
    import_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_import, pattern="^import_json$")],
        states={WAITING_FOR_IMPORT: [MessageHandler(filters.Document.ALL, receive_import_file)]},
        fallbacks=[CommandHandler("cancel", cancel_ai_config)],
    )
    app.add_handler(import_conv)

    # AI config conversation
    ai_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_ai_config, pattern="^config_ai$")],
        states={
            WAITING_FOR_AI_PROVIDER: [
                CallbackQueryHandler(ai_provider_callback, pattern="^ai_provider_"),
                CallbackQueryHandler(cancel_ai_config, pattern="^cancel_ai_config$"),
            ],
            WAITING_FOR_AI_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ai_key),
                CommandHandler("cancel", cancel_ai_config),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_ai_config)],
    )
    app.add_handler(ai_conv)

    logger.info("Bot polling started")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
