import os
import logging
from telegram import (
    Update,
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
import replicate

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Переменные окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")

# База пользователей (упрощённо, в памяти)
user_data = {}

# Приветственное сообщение
WELCOME_TEXT = (
    "👋 Привет! Я бот для генерации и редактирования изображений с помощью "
    "нейросети *Nano Banana (Google Gemini 2.5 Flash ⚡)* — одной из самых мощных моделей.\n\n"
    "✨ У тебя 3 бесплатных генерации.\n\n"
    "Нажми кнопку *«Сгенерировать»* и отправь от 1 до 4 изображений с подписью, "
    "что нужно изменить, или просто напиши текст, чтобы создать новое изображение."
)

# Главное меню (inline)
def main_menu():
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать", callback_data="generate")],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("🛒 Купить", callback_data="buy")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {"credits": 3}
    await update.message.reply_text(WELCOME_TEXT, reply_markup=main_menu(), parse_mode="Markdown")

# Обработка кнопок меню
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "generate":
        await query.message.reply_text("Отправь от 1 до 4 изображений с подписью или просто текст для генерации ✨")
    elif query.data == "balance":
        credits = user_data.get(user_id, {}).get("credits", 0)
        await query.message.reply_text(f"💳 У тебя осталось {credits} генераций")
    elif query.data == "buy":
        await query.message.reply_text("🛒 Покупка пока недоступна (в разработке)")
    elif query.data == "help":
        await query.message.reply_text("❓ Отправь текст, чтобы создать новое изображение, или фото с подписью для редактирования.")

# Обработка текста
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if user_data.get(user_id, {}).get("credits", 0) <= 0:
        await update.message.reply_text("У тебя закончились генерации 😔 Купи дополнительные в меню.")
        return

    await update.message.reply_text("✨ Генерация изображения...")
    try:
        output = replicate.run(
            "google-research/nano-banana:7d2408443b084d5fb34bb64b4a13b6eb3c98603b1f9fbd04b10f526282ef95a6",
            input={"prompt": text}
        )
        if output and isinstance(output, list):
            await update.message.reply_photo(output[0])
            user_data[user_id]["credits"] -= 1
        else:
            await update.message.reply_text("⚠️ Не удалось сгенерировать изображение")
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await update.message.reply_text("⚠️ Ошибка при генерации изображения")

# Обработка фото
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    caption = update.message.caption

    if user_data.get(user_id, {}).get("credits", 0) <= 0:
        await update.message.reply_text("У тебя закончились генерации 😔 Купи дополнительные в меню.")
        return

    if not caption:
        await update.message.reply_text("📌 Пожалуйста, добавь подпись с описанием того, что нужно изменить.")
        return

    await update.message.reply_text("✨ Генерация изображения...")
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        image_url = file.file_path

        output = replicate.run(
            "google-research/nano-banana:7d2408443b084d5fb34bb64b4a13b6eb3c98603b1f9fbd04b10f526282ef95a6",
            input={"prompt": caption, "image": image_url}
        )
        if output and isinstance(output, list):
            await update.message.reply_photo(output[0])
            user_data[user_id]["credits"] -= 1
        else:
            await update.message.reply_text("⚠️ Не удалось сгенерировать изображение")
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await update.message.reply_text("⚠️ Ошибка при генерации изображения")

# Запуск бота
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Webhook (Render)
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 8443)),
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
    )

if __name__ == "__main__":
    main()
