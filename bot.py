import os
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
import replicate

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токены
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL")

# Репликейт клиент
os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN

# Сессии пользователей
user_sessions = {}
FREE_GENERATIONS = 3

# Главное меню
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("✨ Сгенерировать", callback_data="generate")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
        [InlineKeyboardButton("⭐ Купить генерации", callback_data="buy")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Старт
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = {"generations": FREE_GENERATIONS, "photos": [], "prompt": None}
    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений "
        "с помощью нейросети Nano Banana (Google Gemini 2.5 Flash) — одной из самых мощных моделей для генерации изображений.\n\n"
        "✨ У тебя 3 бесплатных генерации.\n\n"
        "Отправьте от 1 до 4 изображений с подписью, что нужно изменить, "
        "или просто напишите текст, чтобы создать новое изображение."
    )
    await update.message.reply_text(text, reply_markup=get_main_menu())

# Баланс
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    gens = user_sessions.get(user_id, {}).get("generations", 0)
    await query.message.reply_text(f"У тебя осталось {gens} генераций.")

# Помощь
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "ℹ️ Я могу:\n"
        "- Генерировать изображения по тексту.\n"
        "- Редактировать до 4-х фото с описанием.\n"
        "- У тебя есть бесплатные генерации, дополнительные можно купить за ⭐."
    )
    await query.message.reply_text(text)

# Купить генерации
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("10 генераций — 40⭐", callback_data="buy_10")],
        [InlineKeyboardButton("50 генераций — 200⭐", callback_data="buy_50")],
        [InlineKeyboardButton("100 генераций — 400⭐", callback_data="buy_100")],
    ]
    await query.message.reply_text("Выбери пакет:", reply_markup=InlineKeyboardMarkup(keyboard))

# Обработка нажатий главного меню
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "balance":
        await balance(update, context)
    elif query.data == "help":
        await help_cmd(update, context)
    elif query.data == "buy":
        await buy(update, context)
    elif query.data == "generate":
        await query.answer()
        await query.message.reply_text(
            "Создавайте и редактируйте изображения прямо в чате.\n\n"
            "Для вас работает Google Gemini 2.5 Flash — она же Nano Banana 🍌\n\n"
            "Готовы начать?\n"
            "Отправьте от 1 до 4 изображений, которые вы хотите изменить, или напишите в чат, что нужно создать"
        )
        await query.message.delete()

# Генерация
async def handle_text_or_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = user_sessions.setdefault(user_id, {"generations": FREE_GENERATIONS, "photos": [], "prompt": None})

    # Текст
    if update.message.text:
        session["prompt"] = update.message.text
    # Фото
    elif update.message.photo:
        photos = update.message.photo
        file_id = photos[-1].file_id
        session["photos"].append(file_id)

    # Проверка условий
    if not session["prompt"] and not session["photos"]:
        return

    if session["photos"] and not session["prompt"]:
        await update.message.reply_text("Пожалуйста, пришли описание к фотографиям.")
        return

    if session["generations"] <= 0:
        await update.message.reply_text("У тебя закончились генерации. Купи новые за ⭐!")
        return

    session["generations"] -= 1
    await update.message.reply_text("⏳ Генерирую изображение...")

    try:
        # Здесь должна быть генерация через Replicate Nano Banana
        output_url = "https://placehold.co/600x400?text=Nano+Banana+Result"

        keyboard = [
            [
                InlineKeyboardButton("🔄 Повторить", callback_data="repeat"),
                InlineKeyboardButton("✅ Завершить", callback_data="finish")
            ]
        ]
        await update.message.reply_photo(photo=output_url, reply_markup=InlineKeyboardMarkup(keyboard))
        await update.message.reply_text("Напишите в чат, если нужно изменить что-то еще")
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await update.message.reply_text("⚠️ Ошибка при генерации изображения.")

# Повторить / Завершить
async def repeat_or_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    session = user_sessions.get(user_id, {})

    if query.data == "repeat":
        if session.get("prompt") or session.get("photos"):
            await query.answer("Повторяю генерацию...")
            await handle_text_or_photo(query, context)
    elif query.data == "finish":
        await query.answer("Сессия завершена.")
        user_sessions[user_id] = {"generations": session.get("generations", 0), "photos": [], "prompt": None}
        await query.message.reply_text("Главное меню:", reply_markup=get_main_menu())

# Вебхук
async def webhook(request):
    from aiohttp import web
    data = await request.json()
    update = Update.de_json(data, context.bot)
    await context.application.process_update(update)
    return web.Response()

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^(balance|help|buy|generate)$"))
    app.add_handler(CallbackQueryHandler(repeat_or_finish, pattern="^(repeat|finish)$"))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_text_or_photo))

    port = int(os.environ.get("PORT", "5000"))
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
