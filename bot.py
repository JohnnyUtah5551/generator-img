# bot.py — версия 1.5 (минимальные добавки: SQLite, лог генераций, уведомления админу, daily report)
import os
import logging
from uuid import uuid4
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import replicate

# === ДОБАВЛЕНИЯ: импорт для SQLite / async wrappers / timezone ===
import sqlite3
import asyncio
from datetime import datetime, timedelta, time, timezone
from typing import Optional, Tuple, List
# === /ДОБАВЛЕНИЯ ===

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Переменные окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")

# Replicate клиент
replicate_client = replicate.Client(api_token=REPLICATE_API_KEY)

# Балансы пользователей (в памяти, как было)
user_balances = {}
FREE_GENERATIONS = 3

# -------------------------
# === ДОБАВЛЕНИЯ: SQLite persistence ===
# -------------------------
# DB path (можно переопределить через env DB_PATH)
DB_PATH = os.environ.get("DB_PATH", "bot.db")

# Строка создания таблиц (минимальная — users + generations)
_INIT_SQL = """
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

def _init_db_sync(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_INIT_SQL)
        conn.commit()
    finally:
        conn.close()

async def init_db():
    # Вызывать до старта приложения (в main)
    await asyncio.to_thread(_init_db_sync, DB_PATH)

# sync helpers
def _ensure_user_sync(user_id: int, username: Optional[str], initial_balance: int = 0):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        # insert if not exists
        cur.execute(
            "INSERT OR IGNORE INTO users (user_id, username, balance, last_active) VALUES (?, ?, ?, ?)",
            (user_id, username, initial_balance, datetime.utcnow().isoformat()),
        )
        # If record existed but balance is NULL, ensure it's 0 (defensive)
        cur.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    finally:
        conn.close()

def _adjust_balance_sync(user_id: int, delta: int) -> Optional[int]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET balance = COALESCE(balance,0) + ? WHERE user_id = ?", (delta, user_id))
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
            (user_id, prompt, typ, result_url, datetime.utcnow().isoformat()),
        )
        cur.execute(
            "UPDATE users SET total_generations = COALESCE(total_generations,0) + 1, last_active = ? WHERE user_id = ?",
            (datetime.utcnow().isoformat(), user_id),
        )
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

# async wrappers
async def ensure_user(user_id: int, username: Optional[str], initial_balance: int = 0):
    await asyncio.to_thread(_ensure_user_sync, user_id, username, initial_balance)

async def adjust_balance(user_id: int, delta: int) -> Optional[int]:
    return await asyncio.to_thread(_adjust_balance_sync, user_id, delta)

async def log_generation(user_id: int, prompt: str, typ: str, result_url: str):
    await asyncio.to_thread(_log_generation_sync, user_id, prompt, typ, result_url)

async def daily_stats_between(start_iso: str, end_iso: str):
    return await asyncio.to_thread(_daily_stats_between_sync, start_iso, end_iso)

# admin notify helper
async def notify_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int, username: Optional[str], prompt: str, result: str, typ: str):
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
# === /ДОБАВЛЕНИЯ: SQLite persistence ===
# -------------------------

# Главное меню
def main_menu():
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать", callback_data="generate")],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("⭐ Купить генерации", callback_data="buy")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

# Генерация изображения через Replicate
async def generate_image(prompt: str, images: list[str] = None):
    try:
        input_data = {"prompt": prompt}
        if images:
            input_data["image"] = images
        output = replicate_client.run(
            "google/nano-banana:9f3b10f33c31d7b8f1dc6f93aef7da71bdf2c1c6d53e11b6c0e4eafd7d7b0b3e",
            input=input_data,
        )
        if isinstance(output, list) and len(output) > 0:
            return output[0]
        return None
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        return None

# Старт
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_balances:
        user_balances[user_id] = FREE_GENERATIONS

    # === ДОБАВЛЕНИЕ: создать запись пользователя в БД (без смены поведения) ===
    # Если пользователя нет в DB, создаём запись с балансом из user_balances (чтобы не менять тексты/логику)
    try:
        await ensure_user(user_id, update.effective_user.username or "", user_balances.get(user_id, FREE_GENERATIONS))
    except Exception:
        logger.exception("Не удалось записать пользователя в SQLite (это не ломает работу)")
    # === /ДОБАВЛЕНИЕ ===

    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью "
        "нейросети Nano Banana (Google Gemini 2.5 Flash ⚡) — одной из самых мощных моделей.\n\n"
        f"✨ У тебя {FREE_GENERATIONS} бесплатных генерации.\n\n"
        "Нажмите кнопку «Сгенерировать» и отправьте от 1 до 4 изображений с подписью, "
        "что нужно изменить, или просто напишите текст, чтобы создать новое изображение."
    )

    await update.message.reply_text(text, reply_markup=main_menu())

# Обработчик меню
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "generate":
        await query.message.reply_text(
            "Создавайте и редактируйте изображения прямо в чате.\n\n"
            "Для вас работает Google Gemini 2.5 Flash — она же Nano Banana 🍌\n\n"
            "Готовы начать?\n"
            "Отправьте от 1 до 4 изображений, которые вы хотите изменить, или напишите в чат, что нужно создать"
        )
        await query.message.delete()

    elif query.data == "balance":
        balance = user_balances.get(query.from_user.id, FREE_GENERATIONS)
        await query.message.reply_text(f"💰 У вас {balance} генераций.", reply_markup=main_menu())

    elif query.data == "buy":
        keyboard = [
            [InlineKeyboardButton("10 генераций — 40⭐", callback_data="buy_10")],
            [InlineKeyboardButton("50 генераций — 200⭐", callback_data="buy_50")],
            [InlineKeyboardButton("100 генераций — 400⭐", callback_data="buy_100")],
        ]
        await query.message.reply_text("Выберите пакет:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "help":
        help_text = (
            "ℹ️ Чтобы сгенерировать изображение, сначала нажмите кнопку «Сгенерировать».\n\n"
            "После этого отправьте от 1 до 4 изображений с подписью, что нужно изменить, "
            "или просто текст для новой картинки.\n\n"
            "💰 Для покупок генераций используется Telegram Stars. "
            "Если у вас их не хватает — пополните через Telegram → Кошелек → Пополнить."
        )
        await query.message.reply_text(help_text, reply_markup=main_menu())

# Покупки
async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    package_map = {
        "buy_10": (10, 40),
        "buy_50": (50, 200),
        "buy_100": (100, 400),
    }

    if query.data in package_map:
        gens, stars = package_map[query.data]

        # Отправляем счёт через Telegram Stars
        await query.message.reply_invoice(
            title="Покупка генераций",
            description=f"{gens} генераций для нейросети",
            payload=f"buy_{gens}",
            provider_token="",  # ❗ Для Stars можно оставить пустым
            currency="XTR",
            prices=[LabeledPrice(label=f"{gens} генераций", amount=stars)],
            start_parameter="stars-payment",
        )

# Обработка успешной оплаты
async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    user_id = update.effective_user.id

    gens_map = {
        "buy_10": 10,
        "buy_50": 50,
        "buy_100": 100,
    }

    gens = gens_map.get(payment.invoice_payload, 0)
    if gens > 0:
        user_balances[user_id] = user_balances.get(user_id, 0) + gens

        # === ДОБАВЛЕНИЕ: отразить пополнение в SQLite ===
        try:
            await adjust_balance(user_id, gens)
        except Exception:
            logger.exception("Ошибка при записи пополнения в SQLite (не ломаем основной поток)")
        # === /ДОБАВЛЕНИЕ ===

        await update.message.reply_text(
            f"✅ Оплата прошла успешно! На ваш баланс добавлено {gens} генераций.",
            reply_markup=main_menu()
        )

# Сообщения с текстом / фото
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = user_balances.get(user_id, FREE_GENERATIONS)

    if balance <= 0:
        await update.message.reply_text(
            "⚠️ У вас закончились генерации. Пополните баланс через меню.",
            reply_markup=main_menu()
        )
        return

    prompt = update.message.caption or update.message.text
    if not prompt:
        await update.message.reply_text("Пожалуйста, добавьте описание для генерации.")
        return

    await update.message.reply_text("⏳ Генерация изображения...")

    images = []
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        images.append(file.file_path)

    result = await generate_image(prompt, images if images else None)

    if result:
        await update.message.reply_photo(result)

        # списываем локальный баланс (как было)
        user_balances[user_id] -= 1

        # === ДОБАВЛЕНИЯ: записать списание и лог генерации в SQLite, уведомить админа ===
        try:
            # reflect deduction in DB
            await adjust_balance(user_id, -1)
        except Exception:
            logger.exception("Ошибка при списании в SQLite (не ломаем основной поток)")

        try:
            # log generation
            await log_generation(user_id, prompt, "image", result)
        except Exception:
            logger.exception("Ошибка при логировании генерации в SQLite")

        # notify admin with prompt + result (image or text)
        try:
            await notify_admin(context, user_id, update.effective_user.username or "", prompt, result, "image")
        except Exception:
            logger.exception("Не удалось уведомить админу о генерации")
        # === /ДОБАВЛЕНИЯ ===

        keyboard = [
            [
                InlineKeyboardButton("🔄 Повторить", callback_data="generate"),
                InlineKeyboardButton("✅ Завершить", callback_data="end"),
            ]
        ]
        await update.message.reply_text(
            "Напишите в чат, если нужно изменить что-то ещё.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.message.reply_text("⚠️ Извините, генерация временно недоступна.")

# Завершение сессии
async def end_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Главное меню:", reply_markup=main_menu())

# Запуск приложения
def main():
    # === ДОБАВЛЕНИЕ: инициализация SQLite (не меняет поведение) ===
    # Вызов асинхронно перед стартом приложения
    try:
        # инициализация синхронного SQL в фоновом потоке
        asyncio.run(_init_db_sync(DB_PATH))
    except Exception:
        # если запуск через asyncio.run плохой в окружении — попробовать sync
        try:
            _init_db_sync(DB_PATH)
        except Exception:
            logger.exception("Не удалось инициализировать SQLite (не ломаем запуск)")
    # === /ДОБАВЛЕНИЕ ===

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^(generate|balance|buy|help)$"))
    app.add_handler(CallbackQueryHandler(buy_handler, pattern="^(buy_10|buy_50|buy_100)$"))
    app.add_handler(CallbackQueryHandler(end_handler, pattern="^end$"))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

    # === ДОБАВЛЕНИЕ: daily report job в 09:00 UTC (==12:00 МСК) ===
    try:
        # job_queue доступен после создания app
        def _schedule_report():
            # run_daily expects time with tzinfo=UTC; create it
            daily_time_utc = time(hour=9, minute=0, tzinfo=timezone.utc)
            app.job_queue.run_daily(_daily_report_job_wrapper, time=daily_time_utc)
        # we add job using app.job_queue directly
        # define wrapper so it has correct signature when called by JobQueue
        async def _daily_report_job_wrapper(context: ContextTypes.DEFAULT_TYPE):
            # compute Moscow day range in UTC and call daily_stats_between + send to admin
            # This uses the same logic as other helpers
            now_utc = datetime.now(timezone.utc)
            moscow_now = now_utc.astimezone(timezone(timedelta(hours=3)))
            moscow_date = moscow_now.date()
            moscow_start = datetime(moscow_date.year, moscow_date.month, moscow_date.day, 0, 0, 0, tzinfo=timezone(timedelta(hours=3)))
            moscow_end = moscow_start + timedelta(days=1)
            utc_start = (moscow_start.astimezone(timezone.utc)).replace(tzinfo=None)
            utc_end = (moscow_end.astimezone(timezone.utc)).replace(tzinfo=None)
            start_iso = utc_start.isoformat()
            end_iso = utc_end.isoformat()

            try:
                total, unique, top = await daily_stats_between(start_iso, end_iso)
            except Exception:
                logger.exception("Ошибка при получении статистики для отчёта")
                total, unique, top = 0, 0, []

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

        # schedule job
        daily_time_utc = time(hour=9, minute=0, tzinfo=timezone.utc)
        app.job_queue.run_daily(_daily_report_job_wrapper, time=daily_time_utc)
    except Exception:
        logger.exception("Не удалось запланировать daily report (не ломаем запуск)")

    # Webhook для Render
    port = int(os.environ.get("PORT", 5000))
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()


