import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# Получаем токены из переменных окружения (безопасно!)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 Привет Напиши, что нарисовать, например:\n\n"
        "• Кот в шляпе\n"
        "• Киберпанк-город\n"
        "• Единорог на радуге\n\n"
        "Я сгенерирую картинку за 10–20 секунд!"
    )

# Обработка текста и генерация изображения
async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = update.message.text
    await update.message.reply_text("🖼️ Генерирую изображение... Подожди немного.")

    # Вызов Replicate API
    response = requests.post(
        "https://api.replicate.com/v1/predictions",
        headers={"Authorization": f"Token {REPLICATE_API_TOKEN}"},
        json={
            "version": "db21e45d3f7023abc2a46ee38a23973f6dce16bb082a930b0c49861f96d1e5bf",  # Stable Diffusion 1.5
            "input": {
                "prompt": prompt,
                "width": 512,
                "height": 512
            }
        }
    )

    if response.status_code != 201:
        await update.message.reply_text("❌ Ошибка генерации. Попробуй другой запрос.")
        return

    prediction = response.json()
    prediction_id = prediction["id"]

    # Ждём готовности изображения
    while True:
        status = requests.get(
            f"https://api.replicate.com/v1/predictions/{prediction_id}",
            headers={"Authorization": f"Token {REPLICATE_API_TOKEN}"}
        ).json()

        if status["status"] == "succeeded":
            await update.message.reply_photo(
                photo=status["output"][0],
                caption=f"✨ Вот твоя картинка по запросу:\n*{prompt}*",
                parse_mode="Markdown"
            )
            break
        elif status["status"] == "failed":
            await update.message.reply_text("❌ Не удалось сгенерировать. Попробуй ещё раз.")
            break
        else:
            import time
            time.sleep(2)  # ждём 2 секунды и проверяем снова

# Запуск бота
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generate_image))

    print("Бот запущен...")
    app.run_polling()
