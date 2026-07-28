import os
import re
import sqlite3
import logging
import asyncio
from typing import Dict, List

import requests
from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
DB_FILE = "nexora_memory.db"

MENU = ReplyKeyboardMarkup(
    [
        ["🤖 AI Manager", "💰 AI CFO"],
        ["🧠 Brand Manager", "📅 Business Planner"],
        ["🎬 Content Creator", "🔥 Viral Ideas"],
        ["🔎 Creator Study", "📊 Analytics"],
        ["📋 CEO Report", "🤝 Executive Meeting"],
        ["📚 Learning Center", "🧰 AI Tool Library"],
        ["💡 Business Ideas", "🎯 Offer Builder"],
        ["🧹 Clear Memory", "🏠 Main Menu"],
    ],
    resize_keyboard=True,
)

BASE_RULES = (
    "You are Nexora AI, a practical AI business assistant. Keep answers clear, direct, useful, and beginner-friendly. "
    "Do not use markdown asterisks. Do not place stars around headings or words. Use plain headings, numbers, emojis, and short paragraphs. "
    "Do not overcomplicate the answer. Give the user the most useful next action first."
)

MODES: Dict[str, str] = {
    "🤖 AI Manager": BASE_RULES + " Act as the user's business manager. Organize priorities, solve problems, make decisions, and create action plans.",
    "💰 AI CFO": BASE_RULES + " Act as a practical CFO. Help with pricing, budgets, expenses, profit, cash flow, goals, forecasts, and simple money plans. State assumptions clearly.",
    "🧠 Brand Manager": BASE_RULES + " Build names, positioning, audiences, messaging, visual direction, brand voice, launch plans, and consistency systems.",
    "📅 Business Planner": BASE_RULES + " Turn goals into realistic daily, weekly, monthly, and 90-day plans. Keep priorities limited and actionable.",
    "🎬 Content Creator": BASE_RULES + " Create captions, hooks, scripts, carousel ideas, short-form videos, long-form videos, emails, posts, and content calendars.",
    "🔥 Viral Ideas": BASE_RULES + " Generate strong viral content concepts based on the user's niche, audience, platform, trend angle, hook, structure, and call to action.",
    "🔎 Creator Study": BASE_RULES + " Study a creator or brand described by the user. Break down their content pillars, hooks, formats, audience, offers, strengths, weaknesses, and lessons to adapt without copying.",
    "📊 Analytics": BASE_RULES + " Analyze performance numbers the user provides. Explain what is working, what is weak, likely causes, and exact changes to test next.",
    "📋 CEO Report": BASE_RULES + " Create a concise CEO report covering wins, problems, money, growth, priorities, risks, and next decisions from information the user provides.",
    "🤝 Executive Meeting": BASE_RULES + " Run a simple executive meeting. Ask only necessary questions, review goals, money, marketing, operations, problems, and finish with decisions and assignments.",
    "📚 Learning Center": BASE_RULES + " Teach the exact topic in simple hands-on steps with examples, mini exercises, and a practical assignment.",
    "🧰 AI Tool Library": BASE_RULES + " Recommend AI tools for the task. Separate free and paid options, explain what each tool does, and give simple setup steps.",
    "💡 Business Ideas": BASE_RULES + " Generate realistic business ideas based on the user's skills, budget, audience, location, time, and income goal. Include how to start and monetize.",
    "🎯 Offer Builder": BASE_RULES + " Turn a skill or service into a clear offer with target customer, problem, result, deliverables, price options, guarantee ideas, and sales message.",
}
DEFAULT_MODE = "🤖 AI Manager"


def db_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_FILE)
    connection.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, mode TEXT NOT NULL)")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    connection.commit()
    return connection


def get_mode(user_id: int) -> str:
    with db_connect() as connection:
        row = connection.execute("SELECT mode FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row and row[0] in MODES else DEFAULT_MODE


def set_mode(user_id: int, mode: str) -> None:
    with db_connect() as connection:
        connection.execute(
            "INSERT INTO users (user_id, mode) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET mode = excluded.mode",
            (user_id, mode),
        )
        connection.commit()


def save_message(user_id: int, role: str, content: str) -> None:
    with db_connect() as connection:
        connection.execute("INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
        connection.commit()


def get_history(user_id: int, limit: int = 16) -> List[dict]:
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    rows.reverse()
    return [{"role": role, "parts": [{"text": content}]} for role, content in rows]


def clear_history(user_id: int) -> None:
    with db_connect() as connection:
        connection.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        connection.commit()


def clean_answer(text: str) -> str:
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"(?m)^\s*\*\s+", "• ", text)
    text = text.replace("*", "")
    return text.strip()


def call_gemini(system_instruction: str, history: List[dict], user_text: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    payload = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": history + [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": 0.75, "maxOutputTokens": 1800},
    }
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no answer")
    parts = candidates[0].get("content", {}).get("parts", [])
    answer = "\n".join(part.get("text", "") for part in parts).strip()
    if not answer:
        raise RuntimeError("Gemini returned an empty answer")
    return clean_answer(answer)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user:
        set_mode(user.id, get_mode(user.id))
    await update.message.reply_text(
        "🚀 Welcome to Nexora AI\n\nChoose what you need below, then send your question.",
        reply_markup=MENU,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Choose a button, then type what you need.\n\nUse Clear Memory to start a fresh conversation.",
        reply_markup=MENU,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_user:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()

    if text == "🏠 Main Menu":
        await update.message.reply_text("🏠 Main menu", reply_markup=MENU)
        return

    if text == "🧹 Clear Memory":
        clear_history(user_id)
        await update.message.reply_text("✅ Memory cleared.", reply_markup=MENU)
        return

    if text in MODES:
        set_mode(user_id, text)
        await update.message.reply_text(f"✅ {text} activated. Send what you need.", reply_markup=MENU)
        return

    mode_name = get_mode(user_id)
    history = get_history(user_id)
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        answer = await asyncio.to_thread(call_gemini, MODES[mode_name], history, text)
        save_message(user_id, "user", text)
        save_message(user_id, "model", answer)
        await update.message.reply_text(answer, reply_markup=MENU)
    except requests.HTTPError as exc:
        logger.exception("Gemini HTTP error")
        status = exc.response.status_code if exc.response is not None else "unknown"
        await update.message.reply_text(f"⚠️ AI connection error: {status}", reply_markup=MENU)
    except Exception:
        logger.exception("Bot error")
        await update.message.reply_text("⚠️ Temporary error. Try again.", reply_markup=MENU)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Telegram error", exc_info=context.error)


def main() -> None:
    db_connect().close()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    print("Nexora AI is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
