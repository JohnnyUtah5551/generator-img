import logging
import os
import sqlite3
import replicate

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

replicate.Client(api_token=REPLICATE_API_TOKEN)

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
MAIN_MENU = [["🎨 Сгенерировать"], ["💳 Баланс", "ℹ️ Помощь"]]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)

    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью "
        "**Nano Banana (Google Gemini 2.5 Flash)** — одной из самых мощных моделей для генерации.\n\n"
        f"✨ У тебя {balance} бесплатных генераций.\n\n"
        "⚡ Для вас работает Google Gemini (Nano Banana).\n\n"
        "**Готовы начать?**\n\n"
        "Отправьте от 1 до 4 изображений, которые вы хотите изменить,\n"
        "или напишите в чат, что нужно создать."
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True),
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)
    await update.message.reply_text(f"💳 У вас осталось {balance} генераций.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Я помогу с генерацией и редактированием изображений.\n\n"
        "1️⃣ Отправьте текстовый запрос или до 4 изображений.\n"
        "2️⃣ Получите результат.\n"
        "3️⃣ Напишите новый текст, чтобы изменить результат.\n\n"
        "Под сгенерированным изображением будут кнопки:\n"
        "— «Сгенерировать другой вариант»\n"
        "— «Закончить генерацию»\n\n"
        "✏️ *Изменить*: если написать новый текст, он применится к последнему изображению.",
        parse_mode="Markdown",
    )


# -----------------------------
# ГЕНЕРАЦИЯ
# -----------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)

    if balance <= 0:
        await update.message.reply_text("❌ У вас закончились генерации.")
        return

    last_image = get_last_image(user_id)
    prompt = update.message.text

    wait_msg = await update.message.reply_text("⏳ Подождите, идёт генерация...")

    try:
        if last_image and context.user_data.get("modify_mode", False):
            output = replicate.run(
                "google/nano-banana",
                input={"prompt": prompt, "image": last_image},
            )
        else:
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


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)
    if balance <= 0:
        await update.message.reply_text("❌ У вас закончились генерации.")
        return

    photos = update.message.photo
    if not photos:
        return

    caption = update.message.caption

    if caption:
        wait_msg = await update.message.reply_text("⏳ Подождите, идёт генерация...")
        try:
            output = replicate.run(
                "google/nano-banana",
                input={"prompt": caption, "image": photos[-1].get_file().file_path},
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
    else:
        await update.message.reply_text("📸 Фото загружено. Напишите описание, чтобы продолжить.")
        context.user_data["pending_photo"] = photos[-1].get_file().file_path


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "regen":
        last_image = get_last_image(user_id)
        if not last_image:
            await query.edit_message_caption("⚠️ Нет последнего изображения для повторной генерации.")
            return

        wait_msg = await query.message.reply_text("⏳ Подождите, идёт генерация...")
        try:
            output = replicate.run(
                "google/nano-banana",
                input={"image": last_image},
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

            await query.message.reply_photo(
                image_url,
                caption="✏️ Напишите в чат, если нужно изменить что-то ещё",
                reply_markup=keyboard,
            )
        finally:
            await wait_msg.delete()

    elif query.data == "end":
        await query.message.reply_text("✅ Генерация завершена.", reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True))


# -----------------------------
# MAIN
# -----------------------------
def main():
    init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
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




