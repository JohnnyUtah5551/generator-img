import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import replicate
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
import replicate

# Получаем токены из переменных окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
PORT = int(os.environ.get("PORT", 5000))  # Render отдаёт порт через переменную PORT
RENDER_URL = os.getenv("RENDER_URL")  # Добавь это в Render → Environment Variables

replicate.Client(api_token=REPLICATE_API_KEY)

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Создать изображение", callback_data="generate")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Привет! Я бот-генератор изображений. Выбери действие:",
        reply_markup=reply_markup,
    )

# Обработка кнопки
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "generate":
        await query.message.reply_text("Генерация изображения...")

        try:
            output = replicate.run(
                "stability-ai/stable-diffusion:db21e45e2a7...",
                input={"prompt": "A futuristic city with flying cars"}
            )
            if isinstance(output, list):
                for url in output:
                    await query.message.reply_photo(photo=url)
            else:
                await query.message.reply_text("Не получилось сгенерировать изображение.")
        except Exception as e:
            await query.message.reply_text(f"Ошибка: {e}")

async def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button))

    # Устанавливаем webhook
    webhook_url = f"{RENDER_URL}/{TELEGRAM_BOT_TOKEN}"
    await application.bot.set_webhook(webhook_url)

    # Запускаем веб-сервер
    await application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=webhook_url,
    )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())



