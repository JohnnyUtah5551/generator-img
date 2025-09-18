import os
import logging
import replicate
from deep_translator import GoogleTranslator
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    PreCheckoutQueryHandler,
)

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")
RENDER_URL = os.getenv("RENDER_URL", "https://generator-img-1.onrender.com")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")

# Хранилище данных пользователей
user_data = {}

# Пакеты для покупки
PACKAGES = {
    "10": {"stars": 40, "count": 10},
    "50": {"stars": 200, "count": 50},
    "100": {"stars": 400, "count": 100},
}


# Функция перевода текста
def translate_prompt(prompt: str) -> str:
    try:
        return GoogleTranslator(source="auto", target="en").translate(prompt)
    except Exception as e:
        logger.error(f"Ошибка перевода: {e}")
        return prompt


# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {"free": 3, "paid": 0}
    await update.message.reply_text(
        "👋 Привет! Я бот для работы с изображениями на основе нейросети "
        "**Google Gemini 2.5 Flash — Nano Banana 🍌**.\n\n"
        "⚡ Nano Banana — одна из самых мощных и современных нейросетей для "
        "генерации и редактирования изображений на сегодняшний день.\n\n"
        "✨ Возможности:\n"
        "• Генерация новых картинок по текстовому описанию.\n"
        "• Редактирование загруженных фотографий (до 4 за раз).\n\n"
        "📌 Доступные команды:\n"
        "/generate — создать или отредактировать изображение\n"
        "/balance — проверить баланс генераций\n"
        "/buy — приобрести дополнительные генерации (по желанию)\n\n"
        "Каждому новому пользователю доступны 3 бесплатные генерации 🎁"
    )


# Баланс
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data.get(user_id, {"free": 0, "paid": 0})
    await update.message.reply_text(
        f"💫 У тебя осталось:\n"
        f"Бесплатных генераций: {data['free']}\n"
        f"Оплаченных генераций: {data['paid']}"
    )


# Покупка генераций
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("10 генераций — 40⭐", callback_data="buy_10"),
        ],
        [
            InlineKeyboardButton("50 генераций — 200⭐", callback_data="buy_50"),
        ],
        [
            InlineKeyboardButton("100 генераций — 400⭐", callback_data="buy_100"),
        ],
    ]
    await update.message.reply_text(
        "Выбери пакет генераций:", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    package = query.data.split("_")[1]
    pack = PACKAGES[package]
    payload = f"buy_{package}"
    await query.message.reply_invoice(
        title=f"{package} генераций",
        description="Пакет генераций для Nano Banana",
        payload=payload,
        provider_token="",  # Telegram Stars не требует токена
        currency="XTR",
        prices=[LabeledPrice(f"{package} генераций", pack["stars"])],
    )


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload
    package = payload.split("_")[1]
    count = PACKAGES[package]["count"]
    user_data[user_id]["paid"] += count
    await update.message.reply_text(f"✅ Покупка успешна! Добавлено {count} генераций.")


# Генерация изображения
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 Отправь описание или фото (до 4 штук).")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {"free": 3, "paid": 0}

    # Проверяем лимит
    if user_data[user_id]["free"] <= 0 and user_data[user_id]["paid"] <= 0:
        await update.message.reply_text(
            "🚫 У тебя закончились генерации.\n"
            "Используй /buy, чтобы приобрести дополнительные."
        )
        return

    prompt = update.message.text or ""
    photos = []
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        photos.append(file.file_path)
    if update.message.document and update.message.document.mime_type.startswith("image/"):
        file = await update.message.document.get_file()
        photos.append(file.file_path)

    # Переводим промт
    if prompt:
        prompt = translate_prompt(prompt)

    try:
        # Запрос в Replicate
        output = replicate.run(
            "google/nano-banana",
            input={"prompt": prompt, "image": photos if photos else None},
        )
        if isinstance(output, list):
            media = [InputMediaPhoto(url) for url in output]
            await update.message.reply_media_group(media)
        else:
            await update.message.reply_photo(output)

        # Списываем генерацию
        if user_data[user_id]["free"] > 0:
            user_data[user_id]["free"] -= 1
        else:
            user_data[user_id]["paid"] -= 1

    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await update.message.reply_text("❌ Ошибка генерации. Попробуй позже.")


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("generate", generate))

    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy_"))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.IMAGE, handle_message))

    app.run_webhook(
        listen="0.0.0.0",
        port=5000,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{WEBHOOK_PATH}"
    )


if __name__ == "__main__":
    main()

