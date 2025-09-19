import os
import logging
from uuid import uuid4
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

# --- Логирование ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Переменные окружения ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

# --- Хранилище сессий ---
sessions = {}

# --- Главное меню ---
def main_menu():
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать", callback_data="generate")],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("⭐ Купить генерации", callback_data="buy")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Старт ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sessions[user_id] = {}
    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью нейросети "
        "Nano Banana (Google Gemini 2.5 Flash) — одной из самых мощных моделей для генерации изображений.\n\n"
        "✨ У тебя 3 бесплатных генерации.\n\n"
        "Отправьте от 1 до 4 изображений с подписью, что нужно изменить, "
        "или просто напишите текст, чтобы создать новое изображение."
    )
    await update.message.reply_text(text, reply_markup=main_menu())

# --- Обработка кнопок ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "generate":
        sessions[user_id] = {"mode": "awaiting_input"}
        await query.message.delete()
        await query.message.reply_text(
            "Создавайте и редактируйте изображения прямо в чате.\n\n"
            "Для вас работает Google Gemini 2.5 Flash — она же Nano Banana 🍌\n\n"
            "Готовы начать?\n"
            "Отправьте от 1 до 4 изображений, которые вы хотите изменить, "
            "или напишите в чат, что нужно создать"
        )

    elif query.data == "balance":
        await query.message.reply_text("У тебя 3 бесплатных генерации. Купленные генерации пока не активированы.")

    elif query.data == "buy":
        keyboard = [
            [InlineKeyboardButton("10 генераций — 40⭐", pay=True)],
            [InlineKeyboardButton("50 генераций — 200⭐", pay=True)],
            [InlineKeyboardButton("100 генераций — 400⭐", pay=True)],
        ]
        await query.message.reply_text("Выберите пакет генераций:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "help":
        await query.message.reply_text(
            "ℹ️ Помощь\n\n"
            "Отправьте текст или фото (1–4 шт.) с описанием — я сгенерирую изображение.\n"
            "После генерации вы сможете повторить или доработать результат.\n\n"
            "Используйте кнопку ⭐ Купить генерации, если закончились бесплатные."
        )

    elif query.data == "repeat":
        session = sessions.get(user_id, {})
        if "last_prompt" in session:
            await generate_image(update, context, session["last_prompt"], session.get("last_photos"))

    elif query.data == "end":
        sessions[user_id] = {}
        await query.message.reply_text("Вы вернулись в главное меню:", reply_markup=main_menu())

# --- Генерация ---
async def generate_image(update, context, prompt, photos=None):
    user_id = update.effective_user.id
    try:
        # Заглушка для вызова модели (т.к. у тебя сейчас нет средств на replicate)
        logger.info(f"Запрос на генерацию. Промт: {prompt}, фото: {photos}")
        fake_url = f"https://placehold.co/600x400?text={prompt.replace(' ', '+')}"
        sessions[user_id]["last_prompt"] = prompt
        sessions[user_id]["last_photos"] = photos

        keyboard = [
            [
                InlineKeyboardButton("🔄 Повторить", callback_data="repeat"),
                InlineKeyboardButton("✅ Завершить", callback_data="end"),
            ]
        ]

        await context.bot.send_photo(
            chat_id=user_id,
            photo=fake_url,
            caption="Напишите в чат, если нужно изменить что-то еще",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await context.bot.send_message(chat_id=user_id, text="❌ Ошибка при генерации изображения.")

# --- Обработка сообщений ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = sessions.get(user_id, {})

    if "mode" in session and session["mode"] == "awaiting_input":
        if update.message.photo:
            if update.message.caption:
                photos = [f"file_id:{p.file_id}" for p in update.message.photo]
                await generate_image(update, context, update.message.caption, photos)
                session["mode"] = "generating"
            else:
                await update.message.reply_text("Пожалуйста, пришлите описание к фото, чтобы я понял, что нужно сделать.")
                session["waiting_for_caption"] = True
        else:
            prompt = update.message.text
            await generate_image(update, context, prompt)
            session["mode"] = "generating"

    elif session.get("waiting_for_caption"):
        photos = session.get("last_photos")
        prompt = update.message.text
        await generate_image(update, context, prompt, photos)
        session["waiting_for_caption"] = False
        session["mode"] = "generating"

    elif session.get("mode") == "generating":
        prompt = update.message.text
        photos = session.get("last_photos")
        await generate_image(update, context, prompt, photos)

# --- Главная функция ---
def main():
    logger.info("🚀 Бот запускается...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=f"{os.getenv('RENDER_URL')}/{TELEGRAM_BOT_TOKEN}",
    )

if __name__ == "__main__":
    main()
