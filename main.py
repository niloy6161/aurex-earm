
import os
import asyncio
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
DATABASE = "aurex_earn.db"

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()


def init_db():
    conn = sqlite3.connect(DATABASE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


@dp.message(CommandStart())
async def start(message: Message):
    conn = sqlite3.connect(DATABASE)
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
        (message.from_user.id, message.from_user.username or "")
    )
    conn.commit()
    conn.close()

    keyboard = None
    if WEBAPP_URL:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🚀 Open Aurex Earn",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        ]])

    await message.answer(
        "Welcome to Aurex Earn!",
        reply_markup=keyboard
    )


async def run_bot():
    if bot:
        await dp.start_polling(bot)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(run_bot()) if bot else None
    yield
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if bot:
        await bot.session.close()


app = FastAPI(title="Aurex Earn", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": "Aurex Earn"}


@app.get("/api")
async def home():
    return {"message": "Aurex Earn is running"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
