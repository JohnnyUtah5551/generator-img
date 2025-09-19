import os
import logging
import replicate
from deep_translator import GoogleTranslator
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ----------------- ЛОГИ -----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------- ПЕРЕМЕННЫЕ -----------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL")

replicate.Client(api_token=REPLICATE_API_KEY)

FREE_GENERATIONS = 3
user_data = {}

# ----------------- КНОПКИ -----------------
def main_menu():
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать", callback_data="generate")],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("⭐ Купить генерации", callback_data="buy")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)


def buy_menu():
    keyboard = [
        [InlineKeyboardButton("10 генераций — 40⭐", callback_data="buy_10")],
        [InlineKeyboardButton("50 генераций — 200⭐", callback_data="buy_50")],
        [InlineKeyboardButton("100 генераций — 400⭐", callback_data="buy_100")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def after_generation_menu():
    keyboard = [
        [
            InlineKeyboardButton("🔁 Повторить", callback_data="repeat"),
            InlineKeyboardButton("✅ Завершить", callback_data="finish"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# ----------------- ХЕНДЛЕРЫ -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {"balance": FREE_GENERATIONS, "last_images": [], "last_prompt": None}

    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью "
        "нейросети Nano Banana (Google Gemini 2.5 Flash⚡️) — одной из самых мощных моделей "
        "для генерации изображений.\n\n"
        "✨ У тебя 3 бесплатных генерации.\n\n"
        "Нажмите кнопку «Сгенерировать» и отправьте от 1 до 4 изображений с подписью, "
        "что нужно изменить, или просто напишите текст, чтобы создать новое изображение."
    )

    await update.message.reply_text(text, reply_markup=main_menu())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data == "generate":
        await query.message.reply_text(
            "Создавайте и редактируйте изображения прямо в чате.\n\n"
            "Для вас работает Google Gemini 2.5 Flash — она же Nano Banana 🍌\n\n"
            "Готовы начать?\n"
            "Отправьте от 1 до 4 изображений, которые вы хотите изменить, "
            "или напишите в чат, что нужно создать"
        )
        await query.message.delete()

    elif data == "balance":
        balance = user_data.get(user_id, {}).get("balance", 0)
        await query.message.reply_text(f"💰 Ваш баланс: {balance} генераций")

    elif data == "buy":
        await query.message.reply_text("Выберите пакет генераций:", reply_markup=buy_menu())

    elif data == "back":
        await query.message.reply_text("Главное меню:", reply_markup=main_menu())

    elif data.startswith("buy_"):
        await query.message.reply_text("⚡ Оплата через Telegram Stars пока в разработке.")

    elif data == "help":
        await query.message.reply_text(
            "❓ Помощь\n\n"
            "— Напишите текст для генерации изображения.\n"
            "— Или отправьте до 4 фото с описанием, чтобы их изменить.\n"
            "— У вас есть 3 бесплатные генерации, далее можно купить ⭐."
        )

    elif data == "repeat":
        last_images = user_data[user_id].get("last_images")
        last_prompt = user_data[user_id].get("last_prompt")
        if last_prompt:
            await generate_image(query, context, last_prompt, last_images)

    elif data == "finish":
        await query.message.reply_text("✅ Сессия завершена.", reply_markup=main_menu())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    prompt = update.message.text

    await generate_image(update, context, prompt)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photos = update.message.photo

    if not photos:
        return

    # сохраняем фото
    file_ids = [p.file_id for p in photos[-4:]]
    user_data[user_id]["last_images"] = file_ids

    if update.message.caption:
        prompt = update.message.caption
        await generate_image(update, context, prompt, file_ids)
    else:
        await update.message.reply_text("✍️ Пожалуйста, пришлите описание для фото.")


async def generate_image(source, context, prompt, images=None):
    user_id = source.effective_user.id
    balance = user_data.get(user_id, {}).get("balance", 0)

    if balance <= 0:
        await source.message.reply_text(
            "У вас закончились генерации. Пополните баланс через «Купить генерации»."
        )
        return

    # Сохраняем данные для повторов
    user_data[user_id]["last_prompt"] = prompt
    if images:
        user_data[user_id]["last_images"] = images

    await source.message.reply_text("🎨 Генерирую изображение...")

    try:
        # Перевод на английский
        translated_prompt = GoogleTranslator(source="auto", target="en").translate(prompt)

        # Здесь должен быть вызов replicate
        # Пока заглушка:
        await source.message.reply_text(
            f"🖼 Сгенерировано по запросу: {prompt}",
            reply_markup=after_generation_menu(),
        )

        # Списываем генерацию
        user_data[user_id]["balance"] -= 1

    except Exception as e:
        logger.error(e)
        await source.message.reply_text("❌ Ошибка при генерации.")


# ----------------- ВЕБХУК -----------------
async def post_init(app: Application):
    # убираем меню справа снизу
    await app.bot.set_my_commands([])
    await app.bot.set_webhook(url=f"{RENDER_URL}/{TELEGRAM_BOT_TOKEN}")


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    app.run_webhook(
        listen="0.0.0.0",
        port=5000,
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=f"{RENDER_URL}/{TELEGRAM_BOT_TOKEN}",
    )


if __name__ == "__main__":
    main()
