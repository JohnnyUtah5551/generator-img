# bot.py — версия 1.5
# Сохранён функционал 1.4 + SQLite + admin notifications + daily report (12:00 MSK)
import os
import logging
import sqlite3
import asyncio
import json
from datetime import datetime, timedelta, time, timezone
from typing import Optional, Tuple, List

import aiohttp
try:
    import replicate
except Exception:
    replicate = None

from deep_translator import GoogleTranslator
from telegram import Update, LabeledPrice
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    PreCheckoutQueryHandler,
)

# -------------------------
# Конфигурация / env vars
# -------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.environ.get("REPLICATE_API_KEY")
REPLICATE_MODEL = os.environ.get("REPLICATE_MODEL", "")  # optional model id, e.g. "stability-ai/stable-diffusion"
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0)
RENDER_URL = os.environ.get("RENDER_URL")  # optional alternative HTTP render endpoint
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("RENDER_URL")
PORT = int(os.environ.get("PORT", "8443"))
DB_PATH = os.environ.get("DB_PATH", "bot.db")
TELEGRAM_STARS_PROVIDER_TOKEN = os.environ.get("TELEGRAM_STARS_PROVIDER_TOKEN", "")  # if used

# Cost settings (adjust to your logic)
COST_PER_IMAGE = int(os.environ.get("COST_PER_IMAGE", "1"))
COST_PER_TEXT = int(os.environ.get("COST_PER_TEXT", "0"))

# Daily report: 12:00 Moscow (UTC+3) => 09:00 UTC
DAILY_REPORT_UTC_TIME = time(hour=9, minute=0, tzinfo=timezone.utc)

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не задан")
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")

# -------------------------
# DB: schema + helpers
# -------------------------
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

# Run blocking DB functions in a thread to not block asyncio loop
async def db_run(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)

# --- Sync DB funcs ---
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
        cur.execute("INSERT OR IGNORE INTO users (user_id, username, last_active) VALUES (?, ?, ?)",
                    (user_id, username, datetime.utcnow().isoformat()))
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
        cur.execute("INSERT INTO generations (user_id, prompt, type, result_url, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, prompt, typ, result_url, datetime.utcnow().isoformat()))
        cur.execute("UPDATE users SET total_generations = total_generations + 1, last_active = ? WHERE user_id = ?",
                    (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    finally:
        conn.close()

def _daily_stats_between_sync(start_iso: str, end_iso: str) -> Tuple[int, int, List[Tuple[int,int]]]:
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

# -------------------------
# Image generation logic
# -------------------------
async def generate_image_via_http(prompt: str) -> Tuple[bool, str]:
    """Fallback HTTP POST to RENDER_URL: expects JSON response with URL in common fields."""
    if not RENDER_URL:
        return False, "RENDER_URL не настроен"
    headers = {"Content-Type": "application/json"}
    payload = {"prompt": prompt}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(RENDER_URL, json=payload, headers=headers, timeout=60) as resp:
                text = await resp.text()
                if resp.status != 200:
                    logger.error("Render returned %s: %s", resp.status, text)
                    return False, f"Render error {resp.status}"
                data = await resp.json()
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
                # fallback
                return False, json.dumps(data)[:1500]
    except asyncio.TimeoutError:
        return False, "Timeout при вызове RENDER_URL"
    except Exception as e:
        logger.exception("Ошибка generate_image_via_http")
        return False, str(e)

async def generate_image(prompt: str) -> Tuple[bool, str]:
    """
    Try Replicate (if configured) -> fallback to RENDER_URL HTTP.
    Returns (success, url_or_error_message).
    """
    # First: try replicate client if available and model configured
    if REPLICATE_API_KEY and replicate:
        try:
            client = replicate.Client(api_token=REPLICATE_API_KEY)
            # If REPLICATE_MODEL env var set, use it; else try to infer a default
            model = REPLICATE_MODEL or None
            if model:
                output = client.run(model, input={"prompt": prompt})
            else:
                # if no model specified, try a commonly used format or fallback
                # NOTE: adjust default model id as you use in your 1.4
                default_model = "stability-ai/stable-diffusion"
                output = client.run(default_model, input={"prompt": prompt})
            # replicate.run may return list or str
            if isinstance(output, list) and output:
                return True, output[0]
            if isinstance(output, str):
                return True, output
            # sometimes output is dict/list with url field
            try:
                # try to parse JSON-like
                if isinstance(output, dict):
                    for k in ("url", "image_url", "result_url"):
                        if k in output:
                            return True, output[k]
            except Exception:
                pass
            return False, "Unexpected response from Replicate"
        except Exception as e:
            logger.exception("Replicate error, falling back to HTTP: %s", e)
            # fall through to HTTP fallback

    # Fallback to http render
    return await generate_image_via_http(prompt)

# -------------------------
# Admin notifications
# -------------------------
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

# -------------------------
# Command handlers
# -------------------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db_run(_create_user_sync, user.id, user.username or "")
    await db_run(_update_last_active_sync, user.id)
    await update.message.reply_text("Привет! Отправь промт для генерации изображения или используй команды.")

async def balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row = await db_run(_get_user_sync, user.id)
    if row:
        _, username, balance, total_generations, last_active = row
        await update.message.reply_text(f"Баланс: {balance}⭐\nВсего генераций: {total_generations}\nПоследняя активность: {last_active}")
    else:
        await update.message.reply_text("Пользователь не найден. Отправь /start")

async def top_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = await db_run(_get_top_users_sync, 10)
    if not top:
        await update.message.reply_text("Нет данных.")
        return
    lines = ["🏆 Топ пользователей (user_id: генераций):"]
    for uid, cnt in top:
        lines.append(f"{uid}: {cnt}")
    await update.message.reply_text("\n".join(lines))

# -------------------------
# Text prompt handler (as in 1.4: text -> image generation)
# -------------------------
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
    await db_run(_create_user_sync, user.id, user.username or "")
    await db_run(_update_last_active_sync, user.id)
    row = await db_run(_get_user_sync, user.id)
    if not row:
        await msg.reply_text("Ошибка доступа к базе.")
        return
    _, _, balance, _, _ = row

    # Check balance
    if balance is None or balance < COST_PER_IMAGE:
        await msg.reply_text("Недостаточно звёзд для генерации. Пополните баланс.")
        return

    # Deduct cost
    await db_run(_adjust_balance_sync, user.id, -COST_PER_IMAGE)
    await msg.reply_text("Начинаю генерацию изображения... (может занять несколько секунд)")

    # Optionally translate prompt to English if you used such logic in 1.4
    try:
        prompt_for_model = GoogleTranslator(source="auto", target="en").translate(prompt)
    except Exception:
        prompt_for_model = prompt

    success, result = await generate_image(prompt_for_model if prompt_for_model else prompt)
    if not success:
        # refund
        await db_run(_adjust_balance_sync, user.id, COST_PER_IMAGE)
        await msg.reply_text(f"Ошибка генерации: {result}")
        return

    # Log generation
    await db_run(_log_generation_sync, user.id, prompt, "image", result)

    # Send result to user
    try:
        lower = (result or "").lower()
        if lower.startswith("http") and any(ext in lower for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]):
            await context.bot.send_photo(chat_id=user.id, photo=result, caption="Готово — вот ваше изображение.")
        else:
            await msg.reply_text(f"Готово — вот результат: {result}")
    except Exception:
        logger.exception("Не удалось отправить результат пользователю")

    # Notify admin (both text and image)
    await notify_admin_about_generation(context, user.id, user.username or "", prompt, result, "image")

# -------------------------
# Payment (Telegram Stars) hooks - placeholders, keep integration points
# -------------------------
async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Called when a payment is successful. Your 1.4 likely already had logic to credit balance.
    Keep this as integration point: adjust crediting according to payload.
    """
    user = update.effective_user
    payload = update.message.successful_payment.invoice_payload
    # Example: payload could be "stars_topup:10"
    try:
        if payload and payload.startswith("topup:"):
            amount = int(payload.split(":", 1)[1])
            await db_run(_adjust_balance_sync, user.id, amount)
            await update.message.reply_text(f"Баланс пополнен на {amount}⭐. Спасибо!")
        else:
            # default behavior: add 1 (customize)
            await db_run(_adjust_balance_sync, user.id, 1)
            await update.message.reply_text("Баланс пополнен. Спасибо!")
    except Exception:
        logger.exception("Ошибка обработки успешной оплаты")
        await update.message.reply_text("Оплата подтверждена, но не удалось обновить ваш баланс. Свяжитесь с админом.")

# -------------------------
# Daily report job
# -------------------------
async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    # compute Moscow day start/end in UTC
    now_utc = datetime.now(timezone.utc)
    moscow_now = now_utc.astimezone(timezone(timedelta(hours=3)))
    moscow_date = moscow_now.date()
    moscow_start = datetime(moscow_date.year, moscow_date.month, moscow_date.day, 0, 0, 0, tzinfo=timezone(timedelta(hours=3)))
    moscow_end = moscow_start + timedelta(days=1)
    utc_start = (moscow_start.astimezone(timezone.utc)).replace(tzinfo=None)
    utc_end = (moscow_end.astimezone(timezone.utc)).replace(tzinfo=None)
    start_iso = utc_start.isoformat()
    end_iso = utc_end.isoformat()

    total, unique, top = await db_run(_daily_stats_between_sync, start_iso, end_iso)
    lines = [
        f"📊 Ежедневный отчёт за {moscow_date.isoformat()} (Москва, UTC+3):",
        f"Всего генераций: {total}",
        f"Уникальных пользователей: {unique}",
    ]
    if top:
        lines.append("Топ-5 пользователей (user_id: количество):")
        for uid, cnt in top:
            lines.append(f"{uid}: {cnt}")
    text = "\n".join(lines)
    try:
        if ADMIN_ID:
            await context.bot.send_message(chat_id=ADMIN_ID, text=text)
        logger.info("Daily report sent for %s", moscow_date.isoformat())
    except Exception:
        logger.exception("Не удалось отправить ежедневный отчёт админу")

# -------------------------
# Startup / Run (WEBHOOK)
# -------------------------
def main():
    init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers (preserve 1.4 handlers)
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("balance", balance_handler))
    app.add_handler(CommandHandler("top", top_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    # Payment handlers (if using Telegram Payments/Stars)
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Schedule daily report at 09:00 UTC (==12:00 MSK)
    app.job_queue.run_daily(daily_report_job, time=DAILY_REPORT_UTC_TIME)

    # Validate webhook base
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL / RENDER_EXTERNAL_URL / RENDER_URL not set. Set WEBHOOK_URL env var to your public base URL.")
        raise RuntimeError("WEBHOOK_URL not configured")

    public_base = WEBHOOK_URL.rstrip("/")
    webhook_full = f"{public_base}/{TELEGRAM_BOT_TOKEN}"  # path includes token for safety

    logger.info("Starting webhook on 0.0.0.0:%s, webhook_url=%s", PORT, webhook_full)
    # run_webhook: provide url_path equal to token (so Telegram posts to /<token>)
    app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TELEGRAM_BOT_TOKEN, webhook_url=webhook_full)

if __name__ == "__main__":
    main()

