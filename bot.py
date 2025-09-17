import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import replicate

# Получаем токены из переменных окружения
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
REPLICATE_API_KEY = os.getenv('REPLICATE_API_KEY')

replicate.api_token = REPLICATE_API_KEY

async def main():
     application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
     
     # Добавьте обработчики команд здесь
     application.add_handler(CommandHandler("start", start))
     application.add_handler(CommandHandler("generate", generate_image))
     
     # Запуск бота
     await application.run_polling()
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Создать изображение", callback_data='generate')]
    ]
try:
    # здесь должен быть ваш код с отступом в 4 пробела
    # например:
    await update.message.reply_text("Привет")
except Exception as e:
    # обработка ошибки тоже должна иметь отступ
    print(f"Произошла ошибка: {e}")
    await update.message.reply_text(f"Произошла ошибка: {str(e)}")
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        'Привет! Я бот-генератор изображений. Выбери действие:',
        reply_markup=reply_markup
    )

# Обработчик нажатий на кнопки
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'generate':
        # Здесь ваш код генерации изображения
        await query.message.reply_text('Генерация изображения...')
        # Добавьте ваш код взаимодействия с Replicate
        # Пример:
        # output = replicate.run(
        #     'stable-diffusion',
        #     input={
        #         "prompt": "your prompt"
        #     }
        # )
        # await query.message.reply_photo(photo=output)

async def main():
    application = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(button))
    
    # Настраиваем Webhook
    await application.bot.set_webhook(url=f'https://your-render-url/{TOKEN}')
    application.run_webhook(
        listen='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        webhook_path=f'/{TOKEN}'
    )

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())


