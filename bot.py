import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    ContextTypes
)
import replicate

# Ваш токен бота
TOKEN = 'ВАШ_ТОКЕН'  # Замените на ваш реальный токен

# Инициализация Replicate
replicate.api_token = 'ВАШ_API_ТОКЕН_REPLICATE'

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Создать изображение", callback_data='generate')]
    ]
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


