import os
import logging
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)
import replicate

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токены
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL")
PORT = int(os.environ.get("PORT", 5000))
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

# Сессии пользователей
user_sessions = {}

# ====== Старт ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {"step": "main"}
    keyboard = [
        [InlineKeyboardButton("Начать генерацию", callback_data="start_generation")],
        [InlineKeyboardButton("Купить 10", callback_data="buy_10")],
        [InlineKeyboardButton("Купить 50", callback_data="buy_50")],
        [InlineKeyboardButton("Купить 100", callback_data="buy_100")]
    ]
    await update.message.reply_text(
        "Привет! Я бот для генерации картинок через Nano Banana 🍌. Выберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ====== Обработка кнопок ======
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "start_generation":
        user_sessions[user_id] = {"step": "waiting_prompt"}
        await query.edit_message_text("Отправь мне текстовый запрос для генерации 🎨")

    elif query.data == "retry":
        session = user_sessions.get(user_id, {})
        if "last_prompt" in session:
            await generate_image(user_id, context, session["last_prompt"], query)
        else:
            await query.edit_message_text("Нет последнего запроса для повтора.")

    elif query.data == "finish":
        user_sessions[user_id] = {"step": "main"}
        keyboard = [
            [InlineKeyboardButton("Начать генерацию", callback_data="start_generation")],
            [InlineKeyboardButton("Купить 10", callback_data="buy_10")],
            [InlineKeyboardButton("Купить 50", callback_data="buy_50")],
            [InlineKeyboardButton("Купить 100", callback_data="buy_100")]
        ]
        await query.edit_message_text(
            "Сессия завершена ✅. Вы снова в главном меню.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data in ["buy_10", "buy_50", "buy_100"]:
        await query.edit_message_text(f"💳 Оплата через Telegram Stars пока в разработке.\nВы выбрали: {query.data}")

# ====== Генерация ======
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = user_sessions.get(user_id, {})

    if session.get("step") == "waiting_prompt":
        prompt = update.message.text
        session["last_prompt"] = prompt
        await generate_image(user_id, context, prompt, update.message)

async def generate_image(user_id, context, prompt, reply_target):
    try:
        await context.bot.send_message(chat_id=user_id, text="Генерирую изображение... ⏳")

        output = replicate.run(
            "google/nano-banana:22db62aaf4b98d4aef5da3e8ad9412601d109e22693f8e1f09143b52a55d2f46",
            input={"prompt": prompt}
        )

        if isinstance(output, list):
            image_url = output[0]
        else:
            image_url = output

        keyboard = [
            [InlineKeyboardButton("Повторить", callback_data="retry")],
            [InlineKeyboardButton("Завершить", callback_data="finish")]
        ]
        await context.bot.send_photo(
            chat_id=user_id,
            photo=image_url,
            caption=f"Результат по запросу:\n`{prompt}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await context.bot.send_message(chat_id=user_id, text="❌ Ошибка при генерации изображения.")

# ====== Основной запуск ======
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск через вебхуки для Render
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
