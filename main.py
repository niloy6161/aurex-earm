
import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    WebAppInfo,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message):
    keyboard = None

    if WEBAPP_URL:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="🚀 Open Aurex Earn",
                    web_app=WebAppInfo(url=WEBAPP_URL)
                )
            ]]
        )

    await message.answer(
        "Welcome to Aurex Earn!\n\n"
        "Complete tasks and explore the app.",
        reply_markup=keyboard
    )


async def run_bot():
    if bot:
        await dp.start_polling(bot)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if bot:
        task = asyncio.create_task(run_bot())

    yield

    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    if bot:
        await bot.session.close()


app = FastAPI(title="Aurex Earn API", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": "Aurex Earn"}


@app.get("/api")
async def home():
    return {"message": "Aurex Earn API is running"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
