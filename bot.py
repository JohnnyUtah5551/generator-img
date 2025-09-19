import logging
import os
import sqlite3
import replicate

from telegram import (
    Update,
    ReplyKeyboardMarkup,
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
# ХЭНДЛЕРЫ
# -----------------------------
MAIN_MENU = [["🎨 Сгенерировать"], ["💳 Баланс", "ℹ️ Помощь"], ["💰 Купить генерации"]]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)

    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью "
        "**Nano Banana (Google Gemini 2.5 Flash)**.\n\n"
        f"✨ У тебя {balance} бесплатных генераций.\n\n"
        "⚡ Для вас работает Google Gemini (Nano Banana).\n\n"
        "📌 Используй кнопки меню снизу."
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True),
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)
    await update.message.reply_text(
        f"💳 У вас осталось {balance} генераций.\n\n"
        "Хотите больше? Нажмите «💰 Купить генерации»."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Я помогу с генерацией и редактированием изображений.\n\n"
        "1️⃣ Отправьте текстовый запрос или до 4 изображений.\n"
        "2️⃣ Получите результат.\n"
        "3️⃣ Напишите новый текст, чтобы изменить результат.\n\n"
        "Под сгенерированным изображением будут кнопки:\n"
        "— «Сгенерировать другой вариант»\n"
        "— «Закончить генерацию»\n\n"
        "Для покупки генераций используйте «💰 Купить генерации».",
        parse_mode="Markdown",
    )


# -----------------------------
# ПОКУПКА ЗВЁЗДАМИ
# -----------------------------
PACKAGES = {
    "pack10": {"label": "10 генераций", "amount": 40000, "generations": 10},   # 40⭐
    "pack50": {"label": "50 генераций", "amount": 200000, "generations": 50},  # 200⭐
    "pack100": {"label": "100 генераций", "amount": 400000, "generations": 100},  # 400⭐
}


async def buy_generations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✨ 10 генераций — 40⭐", callback_data="buy_pack10")],
            [InlineKeyboardButton("⚡ 50 генераций — 200⭐", callback_data="buy_pack50")],
            [InlineKeyboardButton("🚀 100 генераций — 400⭐", callback_data="buy_pack100")],
        ]
    )
    await update.message.reply_text("Выберите пакет генераций:", reply_markup=keyboard)


async def buy_package_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pack_id = query.data.replace("buy_", "")

    if pack_id not in PACKAGES:
        await query.edit_message_text("⚠️ Пакет не найден.")
        return

    package = PACKAGES[pack_id]

    prices = [LabeledPrice(label=package["label"], amount=package["amount"])]

    await context.bot.send_invoice(
        chat_id=query.message.chat_id,
        title="Покупка генераций",
        description=f"{package['label']} для бота",
        payload=pack_id,
        provider_token="",  # для Stars оставляем пустым
        currency="XTR",
        prices=prices,
    )


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload

    if payload in PACKAGES:
        gens = PACKAGES[payload]["generations"]
        update_balance(user_id, gens)
        await update.message.reply_text(
            f"✅ Оплата прошла успешно!\n"
            f"💳 Вам начислено {gens} генераций.\n"
            f"Теперь у вас {get_balance(user_id)} генераций.",
            reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True),
        )
    else:
        await update.message.reply_text("⚠️ Оплата прошла, но пакет не найден.")


# -----------------------------
# ГЕНЕРАЦИЯ (укорочено, как у тебя было)
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

        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔄 Сгенерировать другой вариант", callback_data="regen")],
                [InlineKeyboardButton("✅ Закончить генерацию", callback_data="end")],
            ]
        )

        await update.message.reply_photo(
            image_url,
            caption="✏️ Напишите в чат, если нужно изменить что-то ещё",
            reply_markup=keyboard,
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
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("help", help_command))

    # Покупки
    app.add_handler(MessageHandler(filters.Regex("Купить генерации"), buy_generations))
    app.add_handler(CallbackQueryHandler(buy_package_callback, pattern="^buy_"))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Генерация
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🚀 Бот запускается...")
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=f"{os.environ.get('RENDER_EXTERNAL_URL')}/{TELEGRAM_BOT_TOKEN}",
    )


if __name__ == "__main__":
    main()
