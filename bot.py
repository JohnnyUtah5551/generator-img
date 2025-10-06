import os
import logging
import sqlite3
from datetime import datetime
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

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Переменные окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

# Replicate клиент
replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)

# Настройка базы данных
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
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT id, balance FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users (id, balance, created_at) VALUES (?, ?, ?)",
            (user_id, 3, datetime.utcnow().isoformat()),
        )
        conn.commit()
        balance = 3
    else:
        balance = row[1]
    conn.close()
    return balance


def update_balance(user_id: int, delta: int, tx_type: str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (delta, user_id))
    cur.execute(
        "INSERT INTO transactions (user_id, type, amount, created_at) VALUES (?, ?, ?, ?)",
        (user_id, tx_type, delta, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


# Главное меню
def main_menu():
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать", callback_data="generate")],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("⭐ Купить генерации", callback_data="buy")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)


# Генерация изображения через Replicate с поддержкой нескольких фото
async def generate_image(prompt: str, images: list = None):
    try:
        input_data = {"prompt": prompt}

        # если есть хотя бы одно изображение, берем только первое
        if images and len(images) > 0:
            # images — это URL Telegram файла
            input_data["image_input"] = images[0]

        output = replicate_client.run(
            "google/nano-banana",
            input=input_data,
        )

        if isinstance(output, list) and len(output) > 0:
            return output[0]
        elif isinstance(output, str):
            return output
        else:
            return None

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка генерации: {error_msg}")

        if "insufficient credit" in error_msg.lower():
            return {"error": "Недостаточно генераций. Пополните баланс."}
        elif "flagged as sensitive" in error_msg.lower():
            return {"error": "Запрос отклонён системой модерации. Попробуйте изменить формулировку."}
        else:
            return {"error": "Извините, генерация временно недоступна."}


# Старт
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    get_user(user_id)

    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью "
        "нейросети Nano Banana (Google Gemini 2.5 Flash ⚡).\n\n"
        "✨ У тебя 3 бесплатные генерации.\n\n"
        "Нажмите кнопку «Сгенерировать» и отправьте от 1 до 4 изображений с подписью, "
        "что нужно изменить, или просто напишите текст, чтобы создать новое изображение."
    )

    await update.message.reply_text(text, reply_markup=main_menu())


# Обработчик меню
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception as e:
        if "Query is too old" in str(e):
            logger.warning("Просроченный callback query — можно игнорировать")
        else:
            logger.error(f"Ошибка при ответе на callback: {e}")

    if query.data == "generate":
        await query.message.reply_text(
            "Создавайте и редактируйте изображения прямо в чате.\n\n"
            "Для вас работает Google Gemini 2.5 Flash — она же Nano Banana 🍌\n\n"
            "Отправьте от 1 до 4 изображений с подписью, что нужно изменить, или напишите текст."
        )
        await query.message.delete()

    elif query.data == "balance":
        balance = get_user(query.from_user.id)
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
            "💰 Для покупок генераций используется Telegram Stars."
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
        await query.message.reply_invoice(
            title="Покупка генераций",
            description=f"{gens} генераций для нейросети",
            payload=f"buy_{gens}",
            provider_token="stars",  # ⚡ вот ключевое изменение
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
        update_balance(user_id, gens, "buy")
        await update.message.reply_text(
            f"✅ Оплата прошла успешно! На ваш баланс добавлено {gens} генераций.",
            reply_markup=main_menu()
        )


# Сообщения с текстом / фото
import io
import base64
import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

# ADMIN_ID уже определяется в начале файла через переменные окружения
# ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_user(user_id)

    if user_id != ADMIN_ID and balance <= 0:
        await update.message.reply_text(
            "⚠️ У вас закончились генерации. Пополните баланс через меню.",
            reply_markup=main_menu()
        )
        return

    prompt = update.message.caption or update.message.text
    if not prompt:
        await update.message.reply_text("Пожалуйста, добавьте описание для генерации.")
        return

    progress_msg = await update.message.reply_text("⏳ Генерация изображения...")

    # --- ОБРАБОТКА ФОТО --- #
    images_inputs = []
    if update.message.photo:
        # Берем только самое крупное фото (последнее в списке)
        photo = update.message.photo[-1]
        file = await photo.get_file()  # <- теперь await внутри async
        images_inputs.append(file.file_path)  # передаем URL вместо байто


    # Генерация через Replicate
result = await generate_image(prompt, images_inputs[0] if images_inputs else None)

await progress_msg.delete()

if isinstance(result, dict) and "error" in result:
    await update.message.reply_text(result["error"])
    return

if result:  # result уже URL
    async with httpx.AsyncClient() as client:
        img_bytes = (await client.get(result)).content
    await update.message.reply_photo(img_bytes)
else:
    await update.message.reply_text("⚠️ Извините, генерация временно недоступна.")
    return

if user_id != ADMIN_ID:
    update_balance(user_id, -1, "spend")

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

            
# Завершение сессии
async def end_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Главное меню:", reply_markup=main_menu())


# Отчёты для админа
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), SUM(balance) FROM users")
    users_count, total_balance = cur.fetchone()
    cur.execute("SELECT SUM(amount) FROM transactions WHERE type='buy'")
    total_bought = cur.fetchone()[0] or 0
    cur.execute("SELECT SUM(amount) FROM transactions WHERE type='spend'")
    total_spent = abs(cur.fetchone()[0] or 0)
    conn.close()

    text = (
        f"📊 Статистика:\n\n"
        f"👥 Пользователей: {users_count}\n"
        f"💰 Суммарный баланс: {total_balance}\n"
        f"⭐ Куплено генераций: {total_bought}\n"
        f"🎨 Израсходовано генераций: {total_spent}"
    )
    await update.message.reply_text(text)


# Запуск приложения
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^(generate|balance|buy|help)$"))
    app.add_handler(CallbackQueryHandler(buy_handler, pattern="^(buy_10|buy_50|buy_100)$"))
    app.add_handler(CallbackQueryHandler(end_handler, pattern="^end$"))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

    port = int(os.environ.get("PORT", 5000))
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}"
    )


if __name__ == "__main__":
    main()







