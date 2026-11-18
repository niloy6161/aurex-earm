
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
from pydantic import BaseModel
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "6359796291").split(",")
    if x.strip().isdigit()
]
DATABASE = "aurex_earn.db"
MIN_WITHDRAW = 50

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()


def db():
    return sqlite3.connect(DATABASE)


def init_db(): 
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            account_number TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """) 
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            link TEXT NOT NULL,
            reward REAL NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id, user_id)
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


async def get_verified_user(request: Request):
    user = verify_telegram_data(
        request.headers.get("X-Telegram-Init-Data")
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid Telegram login")
    return user


async def require_admin(request: Request):
    user = await get_verified_user(request)
    if user.get("id") not in ADMIN_IDS:
        raise HTTPException(status_code=403, detail="Admin access denied")
    return user


@dp.message(CommandStart())
async def start(message: Message):
    conn = db()
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
    user = await get_verified_user(request)
    user_id = user["id"]
    username = user.get("username", "")

    conn = db()
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


@app.get("/api/admin/users")
async def admin_users(request: Request):
    await require_admin(request)
    conn = db()
    rows = conn.execute(
        "SELECT user_id, username, balance FROM users ORDER BY user_id DESC"
    ).fetchall()
    conn.close()

    return {
        "total_users": len(rows),
        "users": [
            {"user_id": r[0], "username": r[1], "balance": r[2]}
            for r in rows
        ]
    }


class BalanceUpdate(BaseModel):
    user_id: int
    amount: float
    action: str


@app.post("/api/admin/balance")
async def update_balance(data: BalanceUpdate, request: Request):
    await require_admin(request)

    if data.amount <= 0 or data.action not in ["add", "deduct"]:
        raise HTTPException(status_code=400, detail="Invalid amount or action")

    conn = db()
    row = conn.execute(
        "SELECT balance FROM users WHERE user_id = ?", (data.user_id,)
    ).fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    new_balance = (
        row[0] + data.amount if data.action == "add"
        else row[0] - data.amount
    )

    if new_balance < 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Insufficient balance")

    conn.execute(
        "UPDATE users SET balance = ? WHERE user_id = ?",
        (new_balance, data.user_id)
    )
    conn.commit()
    conn.close()

    return {"success": True, "user_id": data.user_id, "new_balance": new_balance}


class WithdrawRequest(BaseModel):
    amount: float
    method: str
    account_number: str


@app.post("/api/withdraw")
async def create_withdrawal(data: WithdrawRequest, request: Request):
    user = await get_verified_user(request)

    if data.amount < MIN_WITHDRAW:
        raise HTTPException(
            status_code=400,
            detail="Minimum withdrawal is ৳50"
        )

    if data.method not in ["bKash", "Nagad"]:
        raise HTTPException(status_code=400, detail="Invalid payment method")

    number = data.account_number.strip()
    if len(number) < 8 or len(number) > 20:
        raise HTTPException(status_code=400, detail="Invalid payment number")

    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user["id"],)
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        if row[0] < data.amount:
            raise HTTPException(status_code=400, detail="Insufficient balance")

        conn.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (data.amount, user["id"])
        )

        cursor = conn.execute("""
            INSERT INTO withdrawals
            (user_id, amount, method, account_number)
            VALUES (?, ?, ?, ?)
        """, (user["id"], data.amount, data.method, number))

        withdrawal_id = cursor.lastrowid
        conn.commit()

        return {
            "success": True,
            "withdrawal_id": withdrawal_id,
            "status": "pending",
            "message": "Withdrawal request submitted"
        }

    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/my-withdrawals")
async def my_withdrawals(request: Request):
    user = await get_verified_user(request)
    conn = db()
    rows = conn.execute("""
        SELECT id, amount, method, account_number, status, created_at
        FROM withdrawals
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user["id"],)).fetchall()
    conn.close()

    return {
        "withdrawals": [
            {
                "id": r[0],
                "amount": r[1],
                "method": r[2],
                "account_number": r[3],
                "status": r[4],
                "created_at": r[5]
            }
            for r in rows
        ]
    }


@app.get("/api/admin/withdrawals")
async def admin_withdrawals(request: Request):
    await require_admin(request)
    conn = db()
    rows = conn.execute("""
        SELECT id, user_id, amount, method, account_number, status, created_at
        FROM withdrawals
        ORDER BY id DESC
    """).fetchall()
    conn.close()

    return {
        "withdrawals": [
            {
                "id": r[0],
                "user_id": r[1],
                "amount": r[2],
                "method": r[3],
                "account_number": r[4],
                "status": r[5],
                "created_at": r[6]
            }
            for r in rows
        ]
    }


class WithdrawalDecision(BaseModel):
    withdrawal_id: int
    action: str


@app.post("/api/admin/withdrawal-decision")
async def decide_withdrawal(data: WithdrawalDecision, request: Request):
    await require_admin(request)

    if data.action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="Invalid action")

    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute("""
            SELECT user_id, amount, status
            FROM withdrawals WHERE id = ?
        """, (data.withdrawal_id,)).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Request not found")

        user_id, amount, status = row

        if status != "pending":
            raise HTTPException(status_code=400, detail="Request already processed")

        new_status = "approved" if data.action == "approve" else "rejected"

        conn.execute(
            "UPDATE withdrawals SET status = ? WHERE id = ?",
            (new_status, data.withdrawal_id)
        )

        if data.action == "reject":
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (amount, user_id)
            )

        conn.commit()

        notification = (
            f"🎉 Your withdrawal of ৳{amount:.2f} has been approved.\n"
            "Payment will be sent separately."
            if data.action == "approve"
            else f"❌ Your withdrawal of ৳{amount:.2f} was rejected.\n"
                 "The amount has been refunded to your balance."
        )

        if bot:
            try:
                await bot.send_message(user_id, notification)
            except Exception:
                pass

        return {
            "success": True,
            "withdrawal_id": data.withdrawal_id,
            "status": new_status,
            "notification_sent": bool(bot)
        }

    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()




@app.get("/api")
async def home():
    return {"message": "Aurex Earn is running"}

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    link: str
    reward: float


class TaskSubmit(BaseModel):
    task_id: int


class TaskDecision(BaseModel):
    submission_id: int
    action: str


@app.post("/api/admin/tasks")
async def create_task(data: TaskCreate, request: Request):
    await require_admin(request)

    if not data.title.strip() or not data.link.strip() or data.reward <= 0:
        raise HTTPException(status_code=400, detail="Invalid task details")

    conn = db()
    conn.execute("""
        INSERT INTO tasks (title, description, link, reward)
        VALUES (?, ?, ?, ?)
    """, (data.title.strip(), data.description.strip(),
          data.link.strip(), data.reward))
    conn.commit()
    conn.close()

    return {"success": True, "message": "Task created"}


@app.get("/api/tasks")
async def get_tasks(request: Request):
    user = await get_verified_user(request)
    conn = db()

    rows = conn.execute("""
        SELECT t.id, t.title, t.description, t.link, t.reward,
               s.status
        FROM tasks t
        LEFT JOIN task_submissions s
        ON t.id = s.task_id AND s.user_id = ?
        WHERE t.active = 1
        ORDER BY t.id DESC
    """, (user["id"],)).fetchall()

    conn.close()
    return {
        "tasks": [
            {
                "id": r[0],
                "title": r[1],
                "description": r[2],
                "link": r[3],
                "reward": r[4],
                "submission_status": r[5]
            }
            for r in rows
        ]
    }


@app.post("/api/tasks/submit")
async def submit_task(data: TaskSubmit, request: Request):
    user = await get_verified_user(request)
    conn = db()

    task = conn.execute(
        "SELECT id FROM tasks WHERE id = ? AND active = 1",
        (data.task_id,)
    ).fetchone()

    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")

    try:
        conn.execute("""
            INSERT INTO task_submissions (task_id, user_id)
            VALUES (?, ?)
        """, (data.task_id, user["id"]))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(
            status_code=400, detail="Task already submitted"
        )

    conn.close()
    return {"success": True, "status": "pending"}


@app.get("/api/admin/tasks")
async def admin_get_tasks(request: Request):
    await require_admin(request)
    conn = db()
    rows = conn.execute("""
        SELECT id, title, description, link, reward, active
        FROM tasks ORDER BY id DESC
    """).fetchall()
    conn.close()

    return {
        "tasks": [
            {
                "id": r[0], "title": r[1],
                "description": r[2], "link": r[3],
                "reward": r[4], "active": r[5]
            }
            for r in rows
        ]
    }


@app.get("/api/admin/task-submissions")
async def admin_task_submissions(request: Request):
    await require_admin(request)
    conn = db()
    rows = conn.execute("""
        SELECT s.id, s.task_id, t.title, s.user_id,
               u.username, t.reward, s.status, s.created_at
        FROM task_submissions s
        JOIN tasks t ON t.id = s.task_id
        JOIN users u ON u.user_id = s.user_id
        ORDER BY s.id DESC
    """).fetchall()
    conn.close()

    return {
        "submissions": [
            {
                "id": r[0], "task_id": r[1],
                "title": r[2], "user_id": r[3],
                "username": r[4], "reward": r[5],
                "status": r[6], "created_at": r[7]
            }
            for r in rows
        ]
    }


@app.post("/api/admin/task-decision")
async def decide_task(data: TaskDecision, request: Request):
    await require_admin(request)

    if data.action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="Invalid action")

    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute("""
            SELECT s.user_id, s.status, t.reward
            FROM task_submissions s
            JOIN tasks t ON t.id = s.task_id
            WHERE s.id = ?
        """, (data.submission_id,)).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Submission not found")

        user_id, status, reward = row

        if status != "pending":
            raise HTTPException(status_code=400, detail="Already processed")

        new_status = "approved" if data.action == "approve" else "rejected"

        conn.execute(
            "UPDATE task_submissions SET status = ? WHERE id = ?",
            (new_status, data.submission_id)
        )

        if data.action == "approve":
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (reward, user_id)
            )

        conn.commit()

        if bot:
            try:
                if data.action == "approve":
                    await bot.send_message(
                        user_id,
                        f"🎉 Task approved! ৳{reward:.2f} added to your balance."
                    )
                else:
                    await bot.send_message(
                        user_id, "❌ Your task submission was rejected."
                    )
            except Exception:
                pass

        return {"success": True, "status": new_status}

    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


app.mount("/", StaticFiles(directory="static", html=True), name="static")
