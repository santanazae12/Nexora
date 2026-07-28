import os
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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing from your .env file")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing from your .env file")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DB_FILE = "nexora_memory.db"

MENU = ReplyKeyboardMarkup(
    [
        ["🤖 AI Manager", "💰 Autonomous CFO"],
        ["🎬 Content Creator", "📚 Learning Center"],
        ["🧠 Brand Manager", "📅 Business Planner"],
        ["🧹 Clear Memory", "🏠 Main Menu"],
    ],
    resize_keyboard=True,
)

MODES: Dict[str, str] = {
    "🤖 AI Manager": (
        "You are Nexora AI's practical business manager. Help the user make decisions, organize work, "
        "solve business problems, and create simple action plans. Keep answers direct, useful, and easy to follow."
    ),
    "💰 Autonomous CFO": (
        "You are Nexora AI's practical CFO. Help with budgets, pricing, profit, expenses, cash flow, savings goals, "
        "and basic financial planning. Use USD unless the user gives another currency. Never pretend to be a licensed financial adviser."
    ),
    "🎬 Content Creator": (
        "You are Nexora AI's content creator. Create hooks, captions, scripts, faceless-content ideas, YouTube ideas, "
        "TikTok ideas, content calendars, and prompts. Include free and paid AI tools when useful and explain what each tool does."
    ),
    "📚 Learning Center": (
        "You are Nexora AI's hands-on teacher. Teach exactly what the user asks in simple steps, with examples and short exercises. "
        "Recommend useful creators, websites, and tools only when relevant. Keep it visual and ADHD-friendly."
    ),
    "🧠 Brand Manager": (
        "You are Nexora AI's brand manager. Help with brand names, positioning, audience, offers, visual identity, messaging, "
        "launch plans, and social-media consistency."
    ),
    "📅 Business Planner": (
        "You are Nexora AI's business planner. Turn goals into simple daily, weekly, and monthly plans with clear priorities, deadlines, "
        "and next actions. Do not overcomplicate the plan."
    ),
}

DEFAULT_MODE = MODES["🤖 AI Manager"]


def db_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_FILE)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            mode TEXT NOT NULL DEFAULT '🤖 AI Manager'
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.commit()
    return connection


def get_mode(user_id: int) -> str:
    with db_connect() as connection:
        row = connection.execute("SELECT mode FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row[0] if row else "🤖 AI Manager"


def set_mode(user_id: int, mode: str) -> None:
    with db_connect() as connection:
        connection.execute(
            "INSERT INTO users (user_id, mode) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET mode = excluded.mode",
            (user_id, mode),
        )
        connection.commit()


def save_message(user_id: int, role: str, content: str) -> None:
    with db_connect() as connection:
        connection.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        connection.commit()


def get_history(user_id: int, limit: int = 12) -> List[dict]:
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


def call_gemini(system_instruction: str, history: List[dict], user_text: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    contents = history + [{"role": "user", "parts": [{"text": user_text}]}]
    payload = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1200,
        },
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no answer")

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "\n".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise RuntimeError("Gemini returned an empty answer")
    return text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user:
        set_mode(user.id, get_mode(user.id))
    await update.message.reply_text(
        "🚀 Welcome to Nexora AI\n\nChoose a mode below or type any business question.",
        reply_markup=MENU,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🧭 Choose a mode from the menu, then send your question.\n\n"
        "🤖 AI Manager\n💰 Autonomous CFO\n🎬 Content Creator\n📚 Learning Center\n"
        "🧠 Brand Manager\n📅 Business Planner\n\n"
        "Use 🧹 Clear Memory whenever you want a fresh conversation.",
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
        await update.message.reply_text("✅ Your conversation memory has been cleared.", reply_markup=MENU)
        return

    if text in MODES:
        set_mode(user_id, text)
        await update.message.reply_text(f"✅ {text} activated. What do you need?", reply_markup=MENU)
        return

    mode_name = get_mode(user_id)
    system_instruction = MODES.get(mode_name, DEFAULT_MODE)
    history = get_history(user_id)

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        answer = await asyncio.to_thread(call_gemini, system_instruction, history, text)
        save_message(user_id, "user", text)
        save_message(user_id, "model", answer)
        await update.message.reply_text(answer, reply_markup=MENU)
    except requests.HTTPError as exc:
        logger.exception("Gemini HTTP error")
        status = exc.response.status_code if exc.response is not None else "unknown"
        await update.message.reply_text(
            f"⚠️ AI connection error ({status}). Check your Gemini key and model name.",
            reply_markup=MENU,
        )
    except Exception:
        logger.exception("Bot error")
        await update.message.reply_text(
            "⚠️ Temporary error. Please try again.",
            reply_markup=MENU,
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled Telegram error", exc_info=context.error)


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
