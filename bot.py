import logging
import os
import sqlite3
import replicate

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

# -----------------------------
# ЛОГИ
# -----------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# -----------------------------
# НАСТРОЙКИ
# -----------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")
RENDER_URL = os.getenv("RENDER_URL")

replicate.Client(api_token=REPLICATE_API_KEY)

DB_PATH = "bot.db"

# -----------------------------
# БД
# -----------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 3,
                last_image_url TEXT
            )"""
    )
    conn.commit()
    conn.close()


def get_balance(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, 3))
        conn.commit()
        balance = 3
    else:
        balance = row[0]
    conn.close()
    return balance


def update_balance(user_id: int, delta: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (delta, user_id))
    conn.commit()
    conn.close()


def save_last_image(user_id: int, url: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET last_image_url=? WHERE user_id=?", (url, user_id))
    conn.commit()
    conn.close()


def get_last_image(user_id: int) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT last_image_url FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row and row[0] else None


# -----------------------------
# МЕНЮ
# -----------------------------
def main_menu():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎨 Сгенерировать", callback_data="generate")],
            [InlineKeyboardButton("📊 Баланс", callback_data="balance")],
            [InlineKeyboardButton("💰 Купить генерации", callback_data="buy")],
            [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
        ]
    )


# -----------------------------
# ХЭНДЛЕРЫ
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)

    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью нейросети "
        "Nano Banana (Google Gemini 2.5 Flash) — одной из самых мощных моделей для генерации изображений.\n\n"
        "Готовы начать?\n"
        f"✨ У тебя {balance} бесплатных генерации.\n\n"
        "Отправьте от 1 до 4 изображений с подписью, что нужно изменить, "
        "или просто напишите текст, чтобы создать новое изображение."
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "balance":
        balance = get_balance(user_id)
        await query.message.reply_text(f"📊 У вас {balance} генераций.", reply_markup=main_menu())

    elif query.data == "help":
        await query.message.reply_text(
            "ℹ️ Я помогу с генерацией изображений:\n\n"
            "1️⃣ Отправьте текстовый запрос или фото (до 4 штук с подписью).\n"
            "2️⃣ Получите результат.\n"
            "3️⃣ Используйте меню для управления.",
            reply_markup=main_menu(),
        )

    elif query.data == "generate":
        await query.message.reply_text("✏️ Напишите описание картинки или загрузите фото.", reply_markup=main_menu())

    elif query.data == "buy":
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("10 генераций — 40⭐", callback_data="buy_10")],
                [InlineKeyboardButton("50 генераций — 200⭐", callback_data="buy_50")],
                [InlineKeyboardButton("100 генераций — 400⭐", callback_data="buy_100")],
            ]
        )
        await query.message.reply_text("💰 Выберите пакет:", reply_markup=keyboard)

    elif query.data.startswith("buy_"):
        packages = {"buy_10": (10, 40), "buy_50": (50, 200), "buy_100": (100, 400)}
        count, price = packages[query.data]
        title = f"{count} генераций"
        description = f"Пакет из {count} генераций для бота."
        payload = f"purchase_{count}"
        currency = "XTR"
        prices = [LabeledPrice(label=title, amount=price)]
        await context.bot.sendInvoice(
            chat_id=user_id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",  # пусто для Telegram Stars
            currency=currency,
            prices=prices,
        )


# -----------------------------
# ОПЛАТА
# -----------------------------
async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload
    if payload == "purchase_10":
        update_balance(user_id, 10)
    elif payload == "purchase_50":
        update_balance(user_id, 50)
    elif payload == "purchase_100":
        update_balance(user_id, 100)

    await update.message.reply_text("✅ Покупка успешна! Баланс пополнен.", reply_markup=main_menu())


# -----------------------------
# ГЕНЕРАЦИЯ
# -----------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)

    if balance <= 0:
        await update.message.reply_text("❌ У вас закончились генерации.")
        return

    prompt = update.message.text
    wait_msg = await update.message.reply_text("⏳ Подождите, идёт генерация...")

    try:
        output = replicate.run(
            "google/nano-banana",
            input={"prompt": prompt},
        )
        image_url = output[0]
        save_last_image(user_id, image_url)
        update_balance(user_id, -1)

        await update.message.reply_photo(
            image_url,
            caption="✏️ Напишите текст, если нужно изменить изображение",
            reply_markup=main_menu(),
        )
    finally:
        await wait_msg.delete()


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)
    if balance <= 0:
        await update.message.reply_text("❌ У вас закончились генерации.")
        return

    photos = update.message.photo
    if not photos:
        return

    caption = update.message.caption or "Измени это изображение"
    wait_msg = await update.message.reply_text("⏳ Подождите, идёт генерация...")

    try:
        output = replicate.run(
            "google/nano-banana",
            input={"prompt": caption, "image": photos[-1].get_file().file_path},
        )
        image_url = output[0]
        save_last_image(user_id, image_url)
        update_balance(user_id, -1)

        await update.message.reply_photo(
            image_url,
            caption="✏️ Напишите текст, если нужно изменить изображение",
            reply_markup=main_menu(),
        )
    finally:
        await wait_msg.delete()


# -----------------------------
# MAIN
# -----------------------------
def main():
    init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🚀 Бот запускается...")
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=f"{RENDER_URL}/{TELEGRAM_BOT_TOKEN}",
    )


if __name__ == "__main__":
    main()
