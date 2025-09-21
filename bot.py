# bot.py — версия 1.5 (python-telegram-bot v20.6 + SQLite + daily reports + admin notifications)
import os
import logging
import sqlite3
import json
from datetime import datetime, timedelta, time, timezone
import asyncio
from typing import Optional, Tuple, List

import aiohttp
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# --------------------------
# Конфигурация (через env)
# --------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.environ.get("REPLICATE_API_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
RENDER_URL = os.environ.get("RENDER_URL")

DB_PATH = "bot.db"

# время отчёта: 12:00 Москва (UTC+3) -> это 09:00 UTC (Moscow is fixed +3)
DAILY_REPORT_UTC_TIME = time(hour=9, minute=0, second=0, tzinfo=timezone.utc)

# стоимость генерации (звёзды). Подстраивай при необходимости.
COST_PER_IMAGE = 1
COST_PER_TEXT = 0

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не задан")
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")

# --------------------------
# Инициализация БД
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
    type TEXT, -- 'image' или 'text'
    result_url TEXT,
    created_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
"""

def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(INIT_SQL)
        conn.commit()
    finally:
        conn.close()
    logger.info("DB initialized at %s", DB_PATH)

# Утилита для выполнения блокирующих DB-операций в фоне
async def db_run(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)

# Синхронные дб-функции (будут вызваны через db_run)
def _get_user_sync(user_id: int) -> Optional[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, username, balance, total_generations, last_active FROM users WHERE user_id = ?", (user_id,))
        return cur.fetchone()
    finally:
        conn.close()

def _create_user_sync(user_id: int, username: Optional[str]):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO users (user_id, username, last_active) VALUES (?, ?, ?)",
            (user_id, username, datetime.utcnow().isoformat())
        )
        conn.commit()
    finally:
        conn.close()

def _update_last_active_sync(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    finally:
        conn.close()

def _adjust_balance_sync(user_id: int, delta: int) -> Optional[int]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, user_id))
        conn.commit()
        cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return row[0] if row el# bot.py — версия 1.5 (исправлён: WEBHOOK, sqlite, admin notifications, daily report)
import os
import logging
import sqlite3
import json
from datetime import datetime, timedelta, time, timezone
import asyncio
from typing import Optional, Tuple, List

import aiohttp
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# --------------------------
# Конфигурация (через env)
# --------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.environ.get("REPLICATE_API_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
# RENDER_URL здесь — используется для вызовов рендер/репликейт (не для webhook, если задан WEBHOOK_URL)
RENDER_URL = os.environ.get("RENDER_URL")

DB_PATH = os.environ.get("DB_PATH", "bot.db")

# Webhook public URL: обычно задаётся как WEBHOOK_URL (например https://my-app.onrender.com)
# Если не задан — пробуем RENDER_EXTERNAL_URL (Render иногда экспортирует), затем RENDER_URL (используемый ранее)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("RENDER_URL")

# On Render the PORT env var is provided; fallback to 8443
PORT = int(os.environ.get("PORT", "8443"))

# время отчёта: 12:00 Москва (UTC+3) -> это 09:00 UTC
DAILY_REPORT_UTC_TIME = time(hour=9, minute=0, second=0, tzinfo=timezone.utc)

# стоимость генерации (звёзды). Подстрой при необходимости.
COST_PER_IMAGE = 1
COST_PER_TEXT = 0

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не задан")
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")

# --------------------------
# Инициализация БД
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
    type TEXT, -- 'image' или 'text'
    result_url TEXT,
    created_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
"""

def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(INIT_SQL)
        conn.commit()
    finally:
        conn.close()
    logger.info("DB initialized at %s", DB_PATH)

# Утилита для выполнения блокирующих DB-операций в фоне
async def db_run(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)

# Синхронные дб-функции (будут вызваны через db_run)
def _get_user_sync(user_id: int) -> Optional[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, username, balance, total_generations, last_active FROM users WHERE user_id = ?", (user_id,))
        return cur.fetchone()
    finally:
        conn.close()

def _create_user_sync(user_id: int, username: Optional[str]):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO users (user_id, username, last_active) VALUES (?, ?, ?)",
            (user_id, username, datetime.utcnow().isoformat())
        )
        conn.commit()
    finally:
        conn.close()

def _update_last_active_sync(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    finally:
        conn.close()

def _adjust_balance_sync(user_id: int, delta: int) -> Optional[int]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, user_id))
        conn.commit()
        cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()

def _log_generation_sync(user_id: int, prompt: str, typ: str, result_url: str):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO generations (user_id, prompt, type, result_url, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, prompt, typ, result_url, datetime.utcnow().isoformat())
        )
        cur.execute("UPDATE users SET total_generations = total_generations + 1, last_active = ? WHERE user_id = ?",
                    (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    finally:
        conn.close()

def _daily_stats_between_sync(start_iso: str, end_iso: str) -> Tuple[int, int, List[Tuple[int,int]]]:
    """
    Возвращает total, unique, top list для диапазона ISO datetimes (UTC naive strings).
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM generations WHERE created_at >= ? AND created_at < ?", (start_iso, end_iso))
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT user_id) FROM generations WHERE created_at >= ? AND created_at < ?", (start_iso, end_iso))
        unique = cur.fetchone()[0]
        cur.execute("SELECT user_id, COUNT(*) as c FROM generations WHERE created_at >= ? AND created_at < ? GROUP BY user_id ORDER BY c DESC LIMIT 5", (start_iso, end_iso))
        top = cur.fetchall()
        return total, unique, top
    finally:
        conn.close()

def _get_top_users_sync(limit: int = 10) -> List[Tuple[int,int]]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, total_generations FROM users ORDER BY total_generations DESC LIMIT ?", (limit,))
        return cur.fetchall()
    finally:
        conn.close()

# --------------------------
# Генерация изображения (через RENDER_URL / REPLICATE_API_KEY)
# --------------------------
# Подгоняй payload/парсинг под API, который ты используешь.
async def generate_image(prompt: str) -> Tuple[bool, str]:
    if not RENDER_URL or not REPLICATE_API_KEY:
        return False, "RENDER_URL или REPLICATE_API_KEY не настроены"
    headers = {
        "Authorization": f"Token {REPLICATE_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"prompt": prompt}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(RENDER_URL, json=payload, headers=headers, timeout=60) as resp:
                text = await resp.text()
                if resp.status != 200:
                    logger.error("Render returned %s: %s", resp.status, text)
                    return False, f"Render error {resp.status}"
                data = await resp.json()
                # Парсим возможные ключи с URL
                for k in ("url", "result_url", "image_url", "output", "outputs"):
                    if k in data:
                        val = data[k]
                        if isinstance(val, list) and val:
                            if isinstance(val[0], dict):
                                for key in ("url", "image_url", "result_url"):
                                    if key in val[0]:
                                        return True, val[0][key]
                            if isinstance(val[0], str):
                                return True, val[0]
                        else:
                            if isinstance(val, str):
                                return True, val
                            if isinstance(val, dict):
                                for key in ("url", "image_url", "result_url"):
                                    if key in val:
                                        return True, val[key]
                # fallback: сериализуем ответ частично
                return False, json.dumps(data)[:1500]
    except asyncio.TimeoutError:
        return False, "Timeout при вызове RENDER_URL"
    except Exception as e:
        logger.exception("Ошибка generate_image")
        return False, str(e)

# --------------------------
# Уведомления админу (промт + результат) — для текста и картинок
# --------------------------
async def notify_admin_about_generation(context: ContextTypes.DEFAULT_TYPE, user_id: int, username: Optional[str], prompt: str, result: str, typ: str):
    if not ADMIN_ID:
        return
    header = f"👤 Пользователь: @{username or 'none'} (id:{user_id})\n⏱ {datetime.utcnow().isoformat()} UTC\nТип: {typ}\n\nПромт:\n{prompt}\n\n"
    try:
        lower = (result or "").lower()
        if lower.startswith("http") and any(ext in lower for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]):
            await context.bot.send_message(chat_id=ADMIN_ID, text=header + "Результат: (см. фото ниже)")
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=result)
        else:
            preview = result if len(result) < 1400 else result[:1400] + "..."
            await context.bot.send_message(chat_id=ADMIN_ID, text=header + f"Результат:\n{preview}")
    except Exception:
        logger.exception("Не удалось отправить админу уведомление")

# --------------------------
# Хэндлеры команд
# --------------------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db_run(_create_user_sync, user.id, user.username)
    await db_run(_update_last_active_sync, user.id)
    await update.message.reply_text("Привет! Я бот версии 1.5. Отправь промт для генерации изображения.")

async def balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row = await db_run(_get_user_sync, user.id)
    if row:
        _, username, balance, total_generations, last_active = row
        await update.message.reply_text(f"Баланс: {balance}\nВсего генераций: {total_generations}\nПоследняя активность: {last_active}")
    else:
        await update.message.reply_text("Пользователь не найден. Используй /start")

async def top_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = await db_run(_get_top_users_sync, 10)
    if not top:
        await update.message.reply_text("Нет данных.")
        return
    lines = ["🏆 Топ пользователей (user_id: генераций):"]
    for uid, cnt in top:
        lines.append(f"{uid}: {cnt}")
    await update.message.reply_text("\n".join(lines))

# --------------------------
# Обработка текстовых промтов (в 1.4 текст считался запросом на генерацию картинки)
# --------------------------
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    user = update.effective_user
    prompt = msg.text.strip()
    if not prompt:
        await msg.reply_text("Пустой промт.")
        return

    # Ensure user exists
    await db_run(_create_user_sync, user.id, user.username)
    await db_run(_update_last_active_sync, user.id)
    row = await db_run(_get_user_sync, user.id)
    if not row:
        await msg.reply_text("Ошибка БД.")
        return
    _, _, balance, _, _ = row

    # Проверка баланса
    if balance is None or balance < COST_PER_IMAGE:
        await msg.reply_text("Недостаточно звёзд для генерации. Пополните баланс.")
        return

    # Списать баланс
    await db_run(_adjust_balance_sync, user.id, -COST_PER_IMAGE)
    await msg.reply_text("Начинаю генерацию изображения...")

    success, result = await generate_image(prompt)
    if not success:
        # вернуть списанные звёзды
        await db_run(_adjust_balance_sync, user.id, COST_PER_IMAGE)
        await msg.reply_text(f"Ошибка генерации: {result}")
        return

    # Сохранить запись о генерации
    await db_run(_log_generation_sync, user.id, prompt, "image", result)

    # Попытаться отправить пользователю результат
    try:
        lower = (result or "").lower()
        if lower.startswith("http") and any(ext in lower for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]):
            await context.bot.send_photo(chat_id=user.id, photo=result, caption="Готово — вот ваше изображение.")
        else:
            await msg.reply_text(f"Готово — вот ссылка/результат: {result}")
    except Exception:
        logger.exception("Не удалось отправить результат пользователю")

    # Уведомление админу (и для текста, и для картинки)
    await notify_admin_about_generation(context, user.id, user.username, prompt, result, "image")

# --------------------------
# Stars оплата (точка интеграции)
# --------------------------
async def handle_successful_star_payment(user_id: int, amount_stars: int, context: Optional[ContextTypes.DEFAULT_TYPE] = None):
    new_balance = await db_run(_adjust_balance_sync, user_id, amount_stars)
    try:
        if context:
            await context.bot.send_message(chat_id=user_id, text=f"Оплата подтверждена. Ваш баланс пополнен на {amount_stars}⭐. Текущий баланс: {new_balance}")
        else:
            logger.info("Пополнение: user %s +%s (текущий %s)", user_id, amount_stars, new_balance)
    except Exception:
        logger.exception("Не удалось уведомить пользователя о пополнении")

# --------------------------
# Job: ежедневный отчёт
# --------------------------
async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    # Moscow local date:
    now_utc = datetime.now(timezone.utc)
    moscow = now_utc.astimezone(timezone(timedelta(hours=3)))
    date_iso = moscow.date().isoformat()
    # Moscow day start/end in UTC
    moscow_start = datetime(moscow.year, moscow.month, moscow.day, 0, 0, 0, tzinfo=timezone(timedelta(hours=3)))
    moscow_end = moscow_start + timedelta(days=1)
    utc_start = (moscow_start.astimezone(timezone.utc)).replace(tzinfo=None)
    utc_end = (moscow_end.astimezone(timezone.utc)).replace(tzinfo=None)
    start_iso = utc_start.isoformat()
    end_iso = utc_end.isoformat()

    total, unique, top = await db_run(_daily_stats_between_sync, start_iso, end_iso)

    lines = [
        f"📊 Ежедневный отчёт за {date_iso} (Москва, UTC+3):",
        f"Всего генераций: {total}",
        f"Уникальных пользователей: {unique}"
    ]
    if top:
        lines.append("Топ-5 пользователей (user_id: количество):")
        for uid, cnt in top:
            lines.append(f"{uid}: {cnt}")
    text = "\n".join(lines)
    try:
        if ADMIN_ID:
            await context.bot.send_message(chat_id=ADMIN_ID, text=text)
        logger.info("Daily report sent for %s", date_iso)
    except Exception:
        logger.exception("Не удалось отправить ежедневный отчёт админу")

# --------------------------
# Запуск приложения (WEBHOOK)
# --------------------------
def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Регистрируем хэндлеры
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("balance", balance_handler))
    app.add_handler(CommandHandler("top", top_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    # JobQueue: планируем ежедневный отчёт в 09:00 UTC (что равно 12:00 Moscow UTC+3)
    app.job_queue.run_daily(daily_report_job, time=DAILY_REPORT_UTC_TIME)

    # WEBHOOK URL: ожидаем публичный адрес в WEBHOOK_URL (или RENDER_EXTERNAL_URL / RENDER_URL)
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL (или RENDER_EXTERNAL_URL/RENDER_URL) не задан. Установи переменную окружения WEBHOOK_URL с публичным URL приложения.")
        raise RuntimeError("WEBHOOK_URL не задан")

    # Формируем полный URL вебхука — добавляем токен в путь для безопасности
    public_base = WEBHOOK_URL.rstrip("/")
    webhook_full = f"{public_base}/{TELEGRAM_BOT_TOKEN}"

    logger.info("Starting webhook listener on 0.0.0.0:%s -> webhook %s", PORT, webhook_full)
    # Запускаем приложение как webhook service (подходит для Render web service)
    app.run_webhook(listen="0.0.0.0", port=PORT, webhook_url=webhook_full)

if __name__ == "__main__":
    main()


