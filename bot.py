# bot.py — версия 1.5
import os
import logging
import asyncio
import sqlite3
import json
from datetime import datetime, timedelta, timezone

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

# --------------------------
# Конфигурация
# --------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.environ.get("REPLICATE_API_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
RENDER_URL = os.environ.get("RENDER_URL")

DB_PATH = "bot.db"
DAILY_REPORT_HOUR = 12  # 12:00 по Москве (UTC+3)

COST_PER_IMAGE = 1
COST_PER_TEXT = 0

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot)

# --------------------------
# SQLite база
# --------------------------
INIT_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    balance INTEGER DEFAULT 0,
    total_generations INTEGER DEFAULT 0,
    last_active TEXT
);

CREATE TABLE IF NOT EXISTS generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    prompt TEXT,
    type TEXT,
    result_url TEXT,
    created_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
"""

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(INIT_SQL)
    conn.commit()
    conn.close()

async def db_execute(query, params=(), fetch=False, many=False):
    def _execute():
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        if many:
            cur.executemany(query, params)
        else:
            cur.execute(query, params)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        conn.close()
        return rows
    return await asyncio.to_thread(_execute)

# --------------------------
# Генерация изображения
# --------------------------
async def generate_image(prompt: str):
    if not RENDER_URL or not REPLICATE_API_KEY:
        return False, "RENDER_URL или ключ не задан"
    headers = {
        "Authorization": f"Token {REPLICATE_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"prompt": prompt}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(RENDER_URL, json=payload, headers=headers, timeout=60) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return False, f"Ошибка {resp.status}: {data}"
                url = data.get("url") or data.get("result_url") or data.get("image_url")
                if not url and isinstance(data.get("output"), list):
                    url = data["output"][0]
                return True, url
    except Exception as e:
        return False, str(e)

# --------------------------
# Уведомления админу
# --------------------------
async def notify_admin(user: types.User, prompt: str, result: str):
    if not ADMIN_ID:
        return
    text = f"👤 @{user.username or 'none'} (id:{user.id})\n⏱ {datetime.utcnow().isoformat()} UTC\n\nПромт:\n{prompt}"
    try:
        if result.startswith("http"):
            await bot.send_message(ADMIN_ID, text)
            await bot.send_photo(ADMIN_ID, result)
        else:
            await bot.send_message(ADMIN_ID, text + f"\n\nРезультат:\n{result}")
    except Exception:
        pass

# --------------------------
# Ежедневный отчёт
# --------------------------
async def daily_report():
    while True:
        now_utc = datetime.now(timezone.utc)
        moscow = now_utc.astimezone(timezone(timedelta(hours=3)))
        target = moscow.replace(hour=DAILY_REPORT_HOUR, minute=0, second=0, microsecond=0)
        if moscow >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - moscow).total_seconds())

        date_iso = moscow.date().isoformat()
        rows = await db_execute(
            "SELECT COUNT(*), COUNT(DISTINCT user_id) FROM generations WHERE created_at LIKE ?",
            (f"{date_iso}%",),
            fetch=True
        )
        total, unique_users = rows[0]
        top = await db_execute(
            "SELECT user_id, COUNT(*) as c FROM generations WHERE created_at LIKE ? GROUP BY user_id ORDER BY c DESC LIMIT 5",
            (f"{date_iso}%",),
            fetch=True
        )
        lines = [
            f"📊 Отчёт за {date_iso} (МСК)",
            f"Всего генераций: {total}",
            f"Уникальных пользователей: {unique_users}"
        ]
        if top:
            lines.append("Топ-5 пользователей:")
            for uid, c in top:
                lines.append(f"{uid}: {c}")
        await bot.send_message(ADMIN_ID, "\n".join(lines))

# --------------------------
# Хэндлеры
# --------------------------
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    await db_execute(
        "INSERT OR IGNORE INTO users (user_id, username, last_active) VALUES (?, ?, ?)",
        (message.from_user.id, message.from_user.username, datetime.utcnow().isoformat())
    )
    await message.reply("Привет! Отправь мне промт для генерации.")

@dp.message_handler(commands=["balance"])
async def cmd_balance(message: types.Message):
    row = await db_execute("SELECT balance, total_generations FROM users WHERE user_id=?", (message.from_user.id,), fetch=True)
    if row:
        balance, gens = row[0]
        await message.reply(f"Ваш баланс: {balance}\nВсего генераций: {gens}")
    else:
        await message.reply("Вы ещё не зарегистрированы. Используйте /start")

@dp.message_handler(content_types=types.ContentType.TEXT)
async def handle_prompt(message: types.Message):
    user = message.from_user
    prompt = message.text.strip()

    await db_execute(
        "INSERT OR IGNORE INTO users (user_id, username, last_active) VALUES (?, ?, ?)",
        (user.id, user.username, datetime.utcnow().isoformat())
    )

    row = await db_execute("SELECT balance FROM users WHERE user_id=?", (user.id,), fetch=True)
    balance = row[0][0] if row else 0
    if balance < COST_PER_IMAGE:
        await message.reply("Недостаточно звёзд на балансе. Пополните через Telegram Stars.")
        return

    await db_execute("UPDATE users SET balance=balance-? WHERE user_id=?", (COST_PER_IMAGE, user.id))
    await message.reply("Генерация...")

    ok, result = await generate_image(prompt)
    if not ok:
        await db_execute("UPDATE users SET balance=balance+? WHERE user_id=?", (COST_PER_IMAGE, user.id))
        await message.reply(f"Ошибка: {result}")
        return

    await db_execute(
        "INSERT INTO generations (user_id, prompt, type, result_url, created_at) VALUES (?, ?, ?, ?, ?)",
        (user.id, prompt, "image", result, datetime.utcnow().isoformat())
    )
    await db_execute("UPDATE users SET total_generations=total_generations+1 WHERE user_id=?", (user.id,))

    try:
        if result.startswith("http"):
            await bot.send_photo(user.id, result, caption="Готово ✅")
        else:
            await message.reply(f"Результат: {result}")
    except Exception:
        pass

    await notify_admin(user, prompt, result)

# --------------------------
# Stars пополнение
# --------------------------
async def handle_successful_star_payment(user_id: int, amount: int):
    await db_execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
    try:
        await bot.send_message(user_id, f"Ваш баланс пополнен на {amount}⭐")
    except Exception:
        pass

# --------------------------
# Запуск
# --------------------------
async def on_startup(dp):
    init_db()
    asyncio.create_task(daily_report())

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)


