import os
import logging
import sqlite3
import replicate
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from deep_translator import GoogleTranslator

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токены и ключи
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
RENDER_URL = os.getenv("RENDER_URL", "https://generator-img-1.onrender.com")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")

# Подключение к БД
conn = sqlite3.connect("users.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    free_generations INTEGER DEFAULT 3,
    paid_generations INTEGER DEFAULT 0,
    last_image TEXT
)
""")
conn.commit()


def get_or_create_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return (user_id, 3, 0, None)
    return user


def update_user(user_id, **kwargs):
    fields = ", ".join(f"{k}=?" for k in kwargs.keys())
    values = list(kwargs.values())
    values.append(user_id)
    cursor.execute(f"UPDATE users SET {fields} WHERE user_id=?", values)
    conn.commit()


# --- Приветствие-инструкция ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Для вас работает **Google Gemini (Nano Banana) 🍌** — самая мощная нейросеть для генерации изображений на сегодня.\n\n"
        "**Готовы начать?**\n"
        "Отправьте от 1 до 4 изображений, которые вы хотите изменить, или напишите в чат, что нужно создать."
    )
    msg = await update.message.reply_text(text, parse_mode="Markdown")
    context.user_data["instruction_msg_id"] = msg.message_id


# --- Генерация через Replicate ---
async def generate_image(prompt, image_urls=None):
    input_data = {"prompt": prompt}
    if image_urls:
        input_data["input_images"] = image_urls

    output = replicate.run(
        "google/nano-banana",
        input=input_data
    )
    return output[0] if output else None


# --- Обработка текста ---
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = get_or_create_user(user_id)
    prompt = update.message.text.strip()

    # Удаляем инструкцию, если она есть
    if "instruction_msg_id" in context.user_data:
        try:
            await update.message.chat.delete_message(context.user_data["instruction_msg_id"])
        except Exception:
            pass
        context.user_data.pop("instruction_msg_id", None)

    # Показываем "идет генерация"
    waiting_msg = await update.message.reply_text("⏳ Подождите, идёт генерация...")
    context.user_data["waiting_msg_id"] = waiting_msg.message_id

    # Переводим промт
    prompt_en = GoogleTranslator(source="auto", target="en").translate(prompt)

    # Генерация
    image_url = await generate_image(prompt_en)

    # Удаляем "идет генерация"
    try:
        await update.message.chat.delete_message(waiting_msg.message_id)
    except Exception:
        pass

    if image_url:
        update_user(user_id, last_image=image_url)
        keyboard = [
            [InlineKeyboardButton("🔄 Сгенерировать другой вариант", callback_data="retry")],
            [InlineKeyboardButton("✅ Закончить генерацию", callback_data="finish")]
        ]
        await update.message.reply_photo(
            photo=image_url,
            caption="Готово ✅",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text("❌ Ошибка при генерации.")


# --- Обработка фото ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = get_or_create_user(user_id)
    photos = update.message.photo
    caption = update.message.caption

    # Берем максимум 4 фото
    files = []
    for p in photos[-4:]:
        file = await p.get_file()
        files.append(file.file_path)

    if not caption:
        await update.message.reply_text("📌 Пожалуйста, добавьте описание (подпись) к фото.")
        context.user_data["pending_photos"] = files
        return

    # Удаляем инструкцию
    if "instruction_msg_id" in context.user_data:
        try:
            await update.message.chat.delete_message(context.user_data["instruction_msg_id"])
        except Exception:
            pass
        context.user_data.pop("instruction_msg_id", None)

    waiting_msg = await update.message.reply_text("⏳ Подождите, идёт генерация...")
    context.user_data["waiting_msg_id"] = waiting_msg.message_id

    # Переводим промт
    prompt_en = GoogleTranslator(source="auto", target="en").translate(caption)

    # Генерация
    image_url = await generate_image(prompt_en, files)

    try:
        await update.message.chat.delete_message(waiting_msg.message_id)
    except Exception:
        pass

    if image_url:
        update_user(user_id, last_image=image_url)
        keyboard = [
            [InlineKeyboardButton("🔄 Сгенерировать другой вариант", callback_data="retry")],
            [InlineKeyboardButton("✅ Закончить генерацию", callback_data="finish")]
        ]
        await update.message.reply_photo(
            photo=image_url,
            caption="Готово ✅",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text("❌ Ошибка при генерации.")


# --- Callback-и ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user = get_or_create_user(user_id)
    await query.answer()

    if query.data == "retry":
        if not user[3]:
            await query.edit_message_caption("❌ Нет последнего изображения для повторной генерации.")
            return

        waiting_msg = await query.message.reply_text("⏳ Подождите, идёт генерация...")
        image_url = await generate_image("another variation", [user[3]])

        try:
            await waiting_msg.delete()
        except Exception:
            pass

        if image_url:
            update_user(user_id, last_image=image_url)
            keyboard = [
                [InlineKeyboardButton("🔄 Сгенерировать другой вариант", callback_data="retry")],
                [InlineKeyboardButton("✅ Закончить генерацию", callback_data="finish")]
            ]
            await query.message.reply_photo(
                photo=image_url,
                caption="Новый вариант ✅",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    elif query.data == "finish":
        await query.edit_message_caption("✅ Генерация завершена.")


# --- Main ---
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=10000,
        url_path=WEBHOOK_PATH,
        webhook_url=f"{RENDER_URL}/{WEBHOOK_PATH}"
    )


if __name__ == "__main__":
    logger.info("🚀 Бот запускается...")
    main()


