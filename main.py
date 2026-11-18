
import os
import asyncio
import sqlite3
import json
import hmac
import hashlib
import time

from urllib.parse import parse_qsl
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
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


def verify_telegram_data(init_data):
    if not BOT_TOKEN or not init_data:
        return None

    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = data.pop("hash", None)

        if not received_hash:
            return None

        check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(data.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        auth_date = int(data.get("auth_date", "0"))
        if time.time() - auth_date > 86400:
            return None

        return json.loads(data["user"])

    except (ValueError, KeyError, TypeError):
        return None


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


@app.get("/api/me")
async def get_my_balance(request: Request):
    init_data = request.headers.get("X-Telegram-Init-Data")
    user = verify_telegram_data(init_data)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram login"
        )

    user_id = user.get("id")
    username = user.get("username", "")

    conn = sqlite3.connect(DATABASE)

    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
        (user_id, username)
    )

    conn.execute(
        "UPDATE users SET username = ? WHERE user_id = ?",
        (username, user_id)
    )

    row = conn.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    conn.commit()
    conn.close()

    return {
        "user_id": user_id,
        "username": username,
        "balance": row[0] if row else 0,
        "currency": "BDT"
    }


@app.get("/api")
async def home():
    return {"message": "Aurex Earn is running"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
