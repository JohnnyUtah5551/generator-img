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

def _daily_stats_sync(date_iso: str) -> Tuple[int, int, List[Tuple[int,int]]]:
    """
    date_iso: 'YYYY-MM-DD' (Moscow local date or UTC date formatted appropriately).
    Returns: total_generations, unique_users, top_users_list [(user_id, count), ...]
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        like = f"{date_iso}%"
        cur.execute("SELECT COUNT(*) FROM generations WHERE created_at LIKE ?", (like,))
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT user_id) FROM generations WHERE created_at LIKE ?", (like,))
        unique = cur.fetchone()[0]
        cur.execute("SELECT user_id, COUNT(*) as c FROM generations WHERE created_at LIKE ? GROUP BY user_id ORDER BY c DESC LIMIT 5", (like,))
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
                    # Возвращаем ошибку в читаемом виде
                    return False, f"Render error {resp.status}"
                data = await resp.json()
                # Парсим возможные ключи с URL
                for k in ("url", "result_url", "image_url", "output", "outputs"):
                    if k in data:
                        val = data[k]
                        # Если это список, попробуем взять первый элемент
                        if isinstance(val, list) and val:
                            # если элемент — словарь с ключом url
                            if isinstance(val[0], dict):
                                for key in ("url", "image_url", "result_url"):
                                    if key in val[0]:
                                        return True, val[0][key]
                            # если элемент строка — вернуть как есть
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
        # Если result — это URL с расширением изображения, отправляем как фото
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
# Когда приходит подтверждение оплаты звезд (при интеграции с Telegram Stars),
# вызывай handle_successful_star_payment(user_id, amount), чтобы увеличить баланс в базе.
async def handle_successful_star_payment(user_id: int, amount_stars: int, context: Optional[ContextTypes.DEFAULT_TYPE] = None):
    new_balance = await db_run(_adjust_balance_sync, user_id, amount_stars)
    try:
        if context:
            await context.bot.send_message(chat_id=user_id, text=f"Оплата подтверждена. Ваш баланс пополнен на {amount_stars}⭐. Текущий баланс: {new_balance}")
        else:
            # если нет контекста, можно использовать новое приложение для отправки сообщения — но обычно контекст доступен
            logger.info("Пополнение: user %s +%s (текущий %s)", user_id, amount_stars, new_balance)
    except Exception:
        logger.exception("Не удалось уведомить пользователя о пополнении")

# --------------------------
# Задача ежедневного отчёта (JobQueue)
# --------------------------
async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Job, который выполняется раз в сутки (JobQueue будет запущен на 09:00 UTC,
    что соответствует 12:00 Moscow UTC+3).
    """
    # Определяем московскую дату, соответствующую текущему моменту (UTC now)
    now_utc = datetime.now(timezone.utc)
    moscow = now_utc + timedelta(hours=3)
    date_iso = moscow.date().isoformat()  # YYYY-MM-DD (московская дата)
    # Поскольку в БД created_at мы сохраняем UTC ISO timestamps, нам нужно найти строки, у которых дата UTC попадает в диапазон,
    # соответствующий московской дате. Проще: вычислим начало и конец московского дня в UTC и искать по диапазону.
    # Moscow day starts at YYYY-MM-DD 00:00 MSK -> in UTC it's (MSK - 3h)
    moscow_start = datetime(moscow.year, moscow.month, moscow.day, 0, 0, 0, tzinfo=timezone(timedelta(hours=3)))
    moscow_end = moscow_start + timedelta(days=1)
    # Convert to UTC naive ISO strings (we saved created_at as UTC ISO without tzinfo)
    utc_start = (moscow_start.astimezone(timezone.utc)).replace(tzinfo=None)
    utc_end = (moscow_end.astimezone(timezone.utc)).replace(tzinfo=None)
    # We'll query created_at BETWEEN utc_start.isoformat() AND utc_end.isoformat()
    def _stats_between(start_iso: str, end_iso: str):
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
    start_iso = utc_start.isoformat()
    end_iso = utc_end.isoformat()
    total, unique, top = await db_run(_stats_between, start_iso, end_iso)
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
# Запуск приложения
# --------------------------
def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("balance", balance_handler))
    app.add_handler(CommandHandler("top", top_handler))
    # Текстовые сообщения — считаем как запрос на генерацию картинки (как в 1.4)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    # JobQueue: планируем ежедневный отчёт в 09:00 UTC (что равно 12:00 Moscow UTC+3)
    # run_daily принимает время (datetime.time) с tzinfo — передаём DAILY_REPORT_UTC_TIME
    # Параметр days=1 по умолчанию
    app.job_queue.run_daily(daily_report_job, time=DAILY_REPORT_UTC_TIME)

    logger.info("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()

