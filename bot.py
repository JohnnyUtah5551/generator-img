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
PORT = int(os.environ.get("PORT", 5000))
RENDER_URL = os.getenv("RENDER_URL")  # например: https://mybot.onrender.com
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")  # можно указать свой секретный путь

# Инициализируем клиент Replicate
client = replicate.Client(api_token=REPLICATE_API_KEY)


# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("Создать изображение", callback_data="generate")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Привет! Я бот-генератор изображений. Выбери действие:",
        reply_markup=reply_markup,
    )


# Обработчик кнопки
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "generate":
        await query.message.reply_text("Генерация изображения...")

        try:
            output = client.run(
                "stability-ai/stable-diffusion:db21e45e2a7...",  # замени на актуальную модель
                input={"prompt": "A futuristic city with flying cars"},
            )

            if isinstance(output, list):
                for url in output:
                    await query.message.reply_photo(photo=url)
            elif isinstance(output, str):
                await query.message.reply_photo(photo=output)
            else:
                await query.message.reply_text(f"Неожиданный ответ от Replicate: {output}")

        except Exception as e:
            await query.message.reply_text(f"Ошибка: {e}")


# Основная функция
async def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button))

    webhook_url = f"{RENDER_URL}/{WEBHOOK_PATH}"
    await application.bot.set_webhook(webhook_url)

    await application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=webhook_url,
    )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
