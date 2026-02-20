import os
import logging
import sqlite3
import time
import signal
import sys
from datetime import datetime
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    LabeledPrice,
    PreCheckoutQuery,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    PreCheckoutQueryHandler,
)
from telegram.error import Forbidden, TimedOut, NetworkError
import replicate
import requests
from apscheduler.schedulers.background import BackgroundScheduler
import platform

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ====================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

if not TOKEN or not RENDER_URL or not REPLICATE_API_TOKEN:
    logger.error("❌ Не все переменные окружения установлены!")
    sys.exit(1)

logger.info(f"🐍 Python version: {platform.python_version()}")
logger.info(f"🚀 Render URL: {RENDER_URL}")

# ==================== КЛИЕНТ REPLICATE ====================
replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)

# ==================== БАЗА ДАННЫХ ====================
DB_FILE = "bot.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 3,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            amount INTEGER,
            payment_id TEXT UNIQUE,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

def get_user(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT id, balance FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    
    if not row:
        cur.execute(
            "INSERT INTO users (id, balance, created_at) VALUES (?, ?, ?)",
            (user_id, 3, datetime.now().isoformat()),
        )
        conn.commit()
        balance = 3
        logger.info(f"👤 Новый пользователь: {user_id}")
    else:
        balance = row[1]
    
    conn.close()
    return balance

def update_balance(user_id: int, delta: int, tx_type: str, payment_id: str = None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (delta, user_id))
    
    if payment_id:
        cur.execute(
            "INSERT INTO transactions (user_id, type, amount, payment_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, tx_type, delta, payment_id, datetime.now().isoformat())
        )
    else:
        cur.execute(
            "INSERT INTO transactions (user_id, type, amount, created_at) VALUES (?, ?, ?, ?)",
            (user_id, tx_type, delta, datetime.now().isoformat())
        )
    
    conn.commit()
    conn.close()
    logger.info(f"💰 Баланс обновлён: user={user_id}, delta={delta}, type={tx_type}")

# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
async def check_subscription(user_id, bot):
    try:
        member = await bot.get_chat_member(chat_id="@imaigenpromts", user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False

def main_menu():
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать", callback_data="generate")],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("⭐ Купить генерации", callback_data="buy")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== ГЕНЕРАЦИЯ ====================
async def generate_image(prompt: str, images: list = None):
    try:
        input_data = {"prompt": prompt}
        if images:
            input_data["image_input"] = images

        output = replicate_client.run("google/nano-banana", input=input_data)

        if output:
            if isinstance(output, list) and len(output) > 0:
                return output[0]
            return output
        return None
    except Exception as e:
        error_msg = str(e).lower()
        logger.error(f"❌ Ошибка генерации: {error_msg}")
        
        if "insufficient credit" in error_msg:
            return {"error": "⚠️ Недостаточно средств на аккаунте Replicate."}
        elif "flagged as sensitive" in error_msg:
            return {"error": "🚫 Запрос отклонён цензурой. Измените формулировку."}
        else:
            return {"error": "❌ Ошибка при генерации. Попробуйте позже."}

# ==================== КОМАНДЫ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        get_user(user_id)

        text = (
            "👋 Привет! Я бот для генерации изображений с помощью нейросети Nano Banana.\n\n"
            "✨ У тебя 3 бесплатные генерации.\n\n"
            "Нажмите кнопку «Сгенерировать» и отправьте текст или фото с описанием."
        )

        await update.message.reply_text(text, reply_markup=main_menu())
    except Exception as e:
        logger.error(f"❌ Ошибка в start: {e}")

# ==================== ОБРАБОТЧИК КНОПОК ====================
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        
        # ВАЖНО: оборачиваем answer в try/except
        try:
            await query.answer()
        except Exception as e:
            logger.warning(f"⚠️ Не удалось ответить на callback: {e}")
            # Продолжаем выполнение, даже если answer не удался
        
        user_id = query.from_user.id
        logger.info(f"🔘 Нажатие кнопки {query.data} от {user_id}")

        if query.data == "generate":
            balance = get_user(user_id)

            if user_id != ADMIN_ID and balance > 0:
                subscribed = await check_subscription(user_id, context.bot)
                if not subscribed and not context.user_data.get("subscribed_once"):
                    keyboard = [[InlineKeyboardButton("Я подписался ✅", callback_data="confirm_sub")]]
                    await query.message.reply_text(
                        "🎁 Чтобы получить 3 бесплатные генерации, подпишитесь на канал @imaigenpromts",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return

            context.user_data["can_generate"] = True
            await query.message.reply_text("Отправьте текст или фото с описанием.")
            
            try:
                await query.message.delete()
            except:
                pass

        elif query.data == "balance":
            balance = get_user(user_id)
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
                "ℹ️ **Помощь:**\n\n"
                "1. Нажмите «Сгенерировать»\n"
                "2. Отправьте текст или фото\n"
                "3. Получите изображение\n\n"
                "💰 Покупка через Telegram Stars"
            )
            await query.message.reply_text(help_text, parse_mode='Markdown', reply_markup=main_menu())
            
    except Exception as e:
        logger.error(f"❌ Ошибка в menu_handler: {e}")

# ==================== ПОКУПКА ====================
async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        try:
            await query.answer()
        except:
            pass

        packages = {
            "buy_10": {"gens": 10, "stars": 40},
            "buy_50": {"gens": 50, "stars": 200},
            "buy_100": {"gens": 100, "stars": 400},
        }

        if query.data in packages:
            pkg = packages[query.data]
            
            await query.message.reply_invoice(
                title=f"Покупка {pkg['gens']} генераций",
                description=f"Пополнение баланса",
                payload=query.data,
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label=f"{pkg['gens']} генераций", amount=pkg['stars'])],
            )
            
    except Exception as e:
        logger.error(f"❌ Ошибка покупки: {e}")

async def confirm_sub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        try:
            await query.answer()
        except:
            pass

        user_id = query.from_user.id
        subscribed = await check_subscription(user_id, context.bot)

        if subscribed:
            context.user_data["subscribed_once"] = True
            await query.message.edit_text("🎉 Подписка подтверждена!", reply_markup=main_menu())
        else:
            await query.message.reply_text("❌ Вы ещё не подписались!")
    except Exception as e:
        logger.error(f"❌ Ошибка подтверждения: {e}")

async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        payment = update.message.successful_payment
        user_id = update.effective_user.id
        payload = payment.invoice_payload
        payment_id = payment.telegram_payment_charge_id

        gens_map = {"buy_10": 10, "buy_50": 50, "buy_100": 100}
        gens = gens_map.get(payload, 0)
        
        if gens > 0:
            update_balance(user_id, gens, "buy", payment_id)
            await update.message.reply_text(f"✅ Добавлено {gens} генераций!", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"❌ Ошибка обработки платежа: {e}")

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.user_data.get("can_generate"):
            await update.message.reply_text("Главное меню:", reply_markup=main_menu())
            return

        user_id = update.effective_user.id
        balance = get_user(user_id)
        is_admin = user_id == ADMIN_ID

        if not is_admin and balance <= 0:
            await update.message.reply_text("⚠️ Закончились генерации!", reply_markup=main_menu())
            return

        prompt = update.message.caption or update.message.text
        if not prompt:
            await update.message.reply_text("📝 Добавьте описание.")
            return

        await update.message.reply_text("⏳ Генерация...")

        images = []
        if update.message.photo:
            file = await update.message.photo[-1].get_file()
            images = [file.file_path]

        result = await generate_image(prompt, images if images else None)

        if isinstance(result, dict) and "error" in result:
            await update.message.reply_text(result["error"])
            context.user_data["can_generate"] = False
            return

        if result:
            await update.message.reply_photo(result)
            if not is_admin:
                update_balance(user_id, -1, "spend")
        
        context.user_data["can_generate"] = False
        await update.message.reply_text("✅ Готово! Нажмите /start для нового запроса.")
            
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_message: {e}")

# ==================== KEEP-ALIVE ====================
def start_keep_alive():
    scheduler = BackgroundScheduler()
    
    def ping():
        try:
            if RENDER_URL:
                requests.get(f"{RENDER_URL}", timeout=10)
        except:
            pass

    scheduler.add_job(ping, "interval", minutes=5)
    scheduler.start()
    logger.info("✅ Keep-alive запущен")

# ==================== ЗАПУСК ====================
def main():
    init_db()
    
    app = Application.builder().token(TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))

    # Меню
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^(generate|balance|buy|help)$"))
    app.add_handler(CallbackQueryHandler(buy_handler, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(confirm_sub_handler, pattern="^confirm_sub$"))

    # Платежи
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))

    # Keep-alive
    start_keep_alive()
    
    # Запуск
    port = int(os.environ.get("PORT", 10000))
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
