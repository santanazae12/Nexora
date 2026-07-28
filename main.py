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
        ["🧑‍💼 Executive Assistant", "🎬 Content Creator"],
        ["🔥 Viral Ideas", "✍️ Caption Generator"],
        ["🎥 Script Writer", "🗓 Content Calendar"],
        ["💡 Business Ideas", "🎯 Offer Builder"],
        ["💵 Pricing Strategy", "🤝 Sales Assistant"],
        ["📣 Marketing Strategy", "📚 Learning Center"],
        ["🔎 Creator Study", "🧰 AI Tool Library"],
        ["📰 AI News", "📊 Analytics"],
        ["📋 CEO Report", "🤝 Executive Meeting"],
        ["✅ Daily Tasks", "🏆 Goal Planner"],
        ["🧠 View Memory", "🧹 Clear Memory"],
        ["⚙️ Settings", "ℹ️ About Nexora"],
        ["🏠 Main Menu"],
    ],
    resize_keyboard=True,
)

BASE_RULES = (
    "You are Nexora AI, a practical AI business operating assistant. "
    "Keep answers clear, direct, useful, and beginner-friendly. "
    "Never use markdown asterisks. Never place stars around headings or words. "
    "Use plain headings, numbers, emojis, and short paragraphs. "
    "Do not overcomplicate the answer. Give the most useful next action first."
)

MODES: Dict[str, str] = {
    "🤖 AI Manager": BASE_RULES + " Act as the user's business manager. Organize priorities, solve problems, make decisions, and create action plans.",
    "💰 AI CFO": BASE_RULES + " Act as a practical CFO. Help with pricing, budgets, expenses, profit, cash flow, goals, and forecasts. State assumptions clearly.",
    "🧠 Brand Manager": BASE_RULES + " Build brand names, positioning, audiences, messaging, visual direction, brand voice, and launch plans.",
    "📅 Business Planner": BASE_RULES + " Turn business goals into realistic daily, weekly, monthly, and 90-day plans.",
    "🧑‍💼 Executive Assistant": BASE_RULES + " Act as an executive assistant. Organize tasks, write messages, prepare agendas, summarize plans, and keep priorities clear.",
    "🎬 Content Creator": BASE_RULES + " Create posts, captions, hooks, scripts, carousel ideas, emails, and platform-specific content.",
    "🔥 Viral Ideas": BASE_RULES + " Generate strong viral content ideas based on niche, audience, platform, hook, format, and call to action.",
    "✍️ Caption Generator": BASE_RULES + " Write engaging captions with a strong hook, useful body, clear call to action, and relevant hashtags when requested.",
    "🎥 Script Writer": BASE_RULES + " Write short-form and long-form video scripts with hooks, scenes, dialogue, pacing, and calls to action.",
    "🗓 Content Calendar": BASE_RULES + " Build practical content calendars organized by day, platform, topic, format, hook, and purpose.",
    "💡 Business Ideas": BASE_RULES + " Generate realistic business ideas based on skills, budget, audience, time, and income goals.",
    "🎯 Offer Builder": BASE_RULES + " Turn a skill or service into an offer with target customer, problem, result, deliverables, pricing, and sales message.",
    "💵 Pricing Strategy": BASE_RULES + " Help choose prices, packages, payment plans, profit margins, and value-based positioning.",
    "🤝 Sales Assistant": BASE_RULES + " Help write sales messages, discovery questions, objection responses, follow-ups, and closing plans.",
    "📣 Marketing Strategy": BASE_RULES + " Create simple marketing strategies using content, social media, email, partnerships, funnels, and offers.",
    "📚 Learning Center": BASE_RULES + " Teach the exact topic in simple hands-on steps with examples and a practical assignment.",
    "🔎 Creator Study": BASE_RULES + " Study a creator or brand described by the user. Break down content pillars, hooks, formats, audience, offers, strengths, and lessons to adapt without copying.",
    "🧰 AI Tool Library": BASE_RULES + " Recommend useful AI tools. Separate free and paid options and explain simple setup steps.",
    "📰 AI News": BASE_RULES + " Explain AI news or updates provided by the user and what they mean for business owners. Never pretend to have live news unless the user provides it.",
    "📊 Analytics": BASE_RULES + " Analyze performance numbers the user provides. Explain what works, what is weak, and what to test next.",
    "📋 CEO Report": BASE_RULES + " Create a concise CEO report covering wins, problems, money, growth, risks, priorities, and decisions.",
    "🤝 Executive Meeting": BASE_RULES + " Run a simple executive meeting and finish with clear decisions, owners, and next actions.",
    "✅ Daily Tasks": BASE_RULES + " Turn the user's priorities into a short daily task list ordered by importance and impact.",
    "🏆 Goal Planner": BASE_RULES + " Turn a goal into milestones, deadlines, weekly actions, and simple progress checks.",
}

COMMAND_TO_MODE: Dict[str, str] = {
    "manager": "🤖 AI Manager",
    "cfo": "💰 AI CFO",
    "brand": "🧠 Brand Manager",
    "planner": "📅 Business Planner",
    "assistant": "🧑‍💼 Executive Assistant",
    "content": "🎬 Content Creator",
    "viral": "🔥 Viral Ideas",
    "captions": "✍️ Caption Generator",
    "scripts": "🎥 Script Writer",
    "calendar": "🗓 Content Calendar",
    "ideas": "💡 Business Ideas",
    "offers": "🎯 Offer Builder",
    "pricing": "💵 Pricing Strategy",
    "sales": "🤝 Sales Assistant",
    "marketing": "📣 Marketing Strategy",
    "learn": "📚 Learning Center",
    "study": "🔎 Creator Study",
    "tools": "🧰 AI Tool Library",
    "news": "📰 AI News",
    "analytics": "📊 Analytics",
    "report": "📋 CEO Report",
    "meeting": "🤝 Executive Meeting",
    "tasks": "✅ Daily Tasks",
    "goals": "🏆 Goal Planner",
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


def get_memory_count(user_id: int) -> int:
    with db_connect() as connection:
        row = connection.execute("SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,)).fetchone()
    return int(row[0]) if row else 0


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
        "🚀 Welcome to Nexora AI\n\nChoose a business tool below, then send what you need.",
        reply_markup=MENU,
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🏠 Nexora AI main menu", reply_markup=MENU)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    commands = "\n".join(f"/{name}" for name in [
        "start", "menu", "help", "manager", "cfo", "brand", "planner", "assistant",
        "content", "viral", "captions", "scripts", "calendar", "ideas", "offers",
        "pricing", "sales", "marketing", "learn", "study", "tools", "news",
        "analytics", "report", "meeting", "tasks", "goals", "memory",
        "clearmemory", "settings", "about"
    ])
    await update.message.reply_text(
        "Nexora AI commands\n\n" + commands + "\n\nChoose a command, then send what you need.",
        reply_markup=MENU,
    )


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    command = update.message.text.split()[0].lstrip("/").split("@")[0].lower()
    mode = COMMAND_TO_MODE.get(command)
    if not mode:
        return
    set_mode(update.effective_user.id, mode)
    await update.message.reply_text(f"✅ {mode} activated. Send what you need.", reply_markup=MENU)


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    count = get_memory_count(update.effective_user.id)
    mode = get_mode(update.effective_user.id)
    await update.message.reply_text(
        f"🧠 Saved memory items: {count}\nCurrent mode: {mode}",
        reply_markup=MENU,
    )


async def clear_memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    clear_history(update.effective_user.id)
    await update.message.reply_text("✅ Memory cleared.", reply_markup=MENU)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    mode = get_mode(update.effective_user.id)
    await update.message.reply_text(
        f"⚙️ Nexora settings\n\nCurrent mode: {mode}\nAI model: {GEMINI_MODEL}\nMemory: On",
        reply_markup=MENU,
    )


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ℹ️ Nexora AI\n\nYour AI business operating assistant for planning, money, branding, content, marketing, sales, learning, and growth.",
        reply_markup=MENU,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_user:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()

    if text == "🏠 Main Menu":
        await menu_command(update, context)
        return

    if text in {"🧹 Clear Memory", "Clear Memory"}:
        await clear_memory_command(update, context)
        return

    if text == "🧠 View Memory":
        await memory_command(update, context)
        return

    if text == "⚙️ Settings":
        await settings_command(update, context)
        return

    if text == "ℹ️ About Nexora":
        await about_command(update, context)
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
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("help", help_command))

    for command in COMMAND_TO_MODE:
        app.add_handler(CommandHandler(command, mode_command))

    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("clearmemory", clear_memory_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("about", about_command))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("Nexora AI is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
